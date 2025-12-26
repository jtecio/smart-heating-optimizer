"""MQTT handler for receiving setpoint commands from IoT Platform."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Callable

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_NEXT_SETPOINT,
    ATTR_NEXT_SETPOINT_REASON,
    ATTR_NEXT_SETPOINT_TIME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class SetpointCommand:
    """Represents a setpoint command from the IoT Platform."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Initialize the setpoint command."""
        self.temperature_c: float = data.get("temperature_c", 20.0)
        self.valid_from: datetime | None = self._parse_datetime(data.get("valid_from"))
        self.valid_until: datetime | None = self._parse_datetime(data.get("valid_until"))
        self.reason: str = data.get("reason", "optimization")
        self.expected_savings_sek: float = data.get("expected_savings_sek", 0.0)
        self.zone_id: str | None = data.get("zone_id")
        self.raw_data = data

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        """Parse ISO datetime string."""
        if not value:
            return None
        try:
            # Handle both with and without timezone
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None

    def is_valid_now(self) -> bool:
        """Check if the setpoint is valid right now."""
        now = dt_util.utcnow()

        if self.valid_from and now < self.valid_from:
            return False
        if self.valid_until and now > self.valid_until:
            return False
        return True

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"SetpointCommand(temp={self.temperature_c}C, "
            f"from={self.valid_from}, until={self.valid_until}, "
            f"reason={self.reason})"
        )


class SmartHeatingMQTTHandler:
    """Handle MQTT messages for Smart Heating Optimizer."""

    def __init__(
        self,
        hass: HomeAssistant,
        installation_id: str,
        zones: dict[str, dict[str, Any]],
        on_setpoint_callback: Callable[[str, SetpointCommand], None] | None = None,
    ) -> None:
        """Initialize the MQTT handler.

        Args:
            hass: Home Assistant instance
            installation_id: The installation ID for topic subscription
            zones: Dict mapping zone_id to zone config (with climate_entity_id etc)
            on_setpoint_callback: Optional callback when setpoint is applied
        """
        self._hass = hass
        self._installation_id = installation_id
        self._zones = zones  # {zone_id: {climate_entity_id: ..., auto_control: ...}}
        self._on_setpoint_callback = on_setpoint_callback
        self._unsubscribe: Callable[[], None] | None = None
        self._pending_setpoints: dict[str, SetpointCommand] = {}
        self._scheduled_unsubs: dict[str, Callable[[], None]] = {}
        self._applied_setpoints: dict[str, SetpointCommand] = {}

    @property
    def is_subscribed(self) -> bool:
        """Return True if subscribed to MQTT."""
        return self._unsubscribe is not None

    def get_pending_setpoint(self, zone_id: str) -> SetpointCommand | None:
        """Get the pending setpoint for a zone."""
        return self._pending_setpoints.get(zone_id)

    def get_applied_setpoint(self, zone_id: str) -> SetpointCommand | None:
        """Get the last applied setpoint for a zone."""
        return self._applied_setpoints.get(zone_id)

    async def async_subscribe(self) -> bool:
        """Subscribe to MQTT setpoint topics.

        Returns:
            True if subscription was successful, False otherwise.
        """
        if not await mqtt.async_wait_for_mqtt_client(self._hass):
            _LOGGER.warning("MQTT client not available, cannot subscribe to setpoints")
            return False

        # Subscribe to all zone setpoints for this installation
        # Topic pattern: ha/{installation_id}/zone/+/setpoint
        topic = f"ha/{self._installation_id}/zone/+/setpoint"

        try:
            self._unsubscribe = await mqtt.async_subscribe(
                self._hass,
                topic,
                self._handle_setpoint_message,
                qos=1,
            )
            _LOGGER.info("Subscribed to MQTT topic: %s", topic)
            return True
        except Exception as err:
            _LOGGER.error("Failed to subscribe to MQTT topic %s: %s", topic, err)
            return False

    async def async_unsubscribe(self) -> None:
        """Unsubscribe from MQTT topics."""
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
            _LOGGER.info("Unsubscribed from MQTT setpoint topics")

        # Cancel any scheduled setpoints
        for zone_id, unsub in self._scheduled_unsubs.items():
            unsub()
        self._scheduled_unsubs.clear()

    @callback
    def _handle_setpoint_message(self, msg: mqtt.ReceiveMessage) -> None:
        """Handle incoming MQTT setpoint message."""
        try:
            # Extract zone_id from topic: ha/{installation_id}/zone/{zone_id}/setpoint
            topic_parts = msg.topic.split("/")
            if len(topic_parts) < 5:
                _LOGGER.warning("Invalid topic format: %s", msg.topic)
                return

            zone_id = topic_parts[3]

            # Parse payload
            try:
                payload = json.loads(msg.payload)
            except json.JSONDecodeError:
                _LOGGER.error("Invalid JSON payload: %s", msg.payload)
                return

            setpoint = SetpointCommand(payload)
            setpoint.zone_id = zone_id

            _LOGGER.info(
                "Received setpoint for zone %s: %s",
                zone_id,
                setpoint
            )

            # Check if zone exists and has auto-control enabled
            zone_config = self._zones.get(zone_id)
            if not zone_config:
                _LOGGER.warning("Received setpoint for unknown zone: %s", zone_id)
                return

            if not zone_config.get("auto_control", True):
                _LOGGER.info(
                    "Auto-control disabled for zone %s, ignoring setpoint",
                    zone_id
                )
                return

            # Store and schedule the setpoint
            self._hass.async_create_task(
                self._process_setpoint(zone_id, setpoint, zone_config)
            )

        except Exception as err:
            _LOGGER.error("Error handling setpoint message: %s", err)

    async def _process_setpoint(
        self,
        zone_id: str,
        setpoint: SetpointCommand,
        zone_config: dict[str, Any],
    ) -> None:
        """Process and potentially apply a setpoint."""
        climate_entity_id = zone_config.get("climate_entity_id")
        if not climate_entity_id:
            _LOGGER.warning("No climate entity configured for zone %s", zone_id)
            return

        # Store as pending
        self._pending_setpoints[zone_id] = setpoint

        # Check if setpoint is valid now
        if setpoint.is_valid_now():
            await self._apply_setpoint(zone_id, setpoint, climate_entity_id)
        else:
            # Schedule for later if valid_from is in the future
            if setpoint.valid_from:
                self._schedule_setpoint(zone_id, setpoint, climate_entity_id)

    async def _apply_setpoint(
        self,
        zone_id: str,
        setpoint: SetpointCommand,
        climate_entity_id: str,
    ) -> None:
        """Apply a setpoint to the climate entity."""
        try:
            # Check if climate entity exists
            state = self._hass.states.get(climate_entity_id)
            if not state:
                _LOGGER.error("Climate entity not found: %s", climate_entity_id)
                return

            # Get current temperature
            current_temp = state.attributes.get("temperature")

            _LOGGER.info(
                "Applying setpoint to %s: %s -> %s (reason: %s, savings: %.2f SEK)",
                climate_entity_id,
                current_temp,
                setpoint.temperature_c,
                setpoint.reason,
                setpoint.expected_savings_sek,
            )

            # Apply the setpoint via climate service
            await self._hass.services.async_call(
                "climate",
                "set_temperature",
                {
                    "entity_id": climate_entity_id,
                    "temperature": setpoint.temperature_c,
                },
                blocking=True,
            )

            # Store as applied
            self._applied_setpoints[zone_id] = setpoint

            # Remove from pending
            if zone_id in self._pending_setpoints:
                del self._pending_setpoints[zone_id]

            # Fire event for logging/tracking
            self._hass.bus.async_fire(
                f"{DOMAIN}_setpoint_applied",
                {
                    "zone_id": zone_id,
                    "climate_entity_id": climate_entity_id,
                    "temperature_c": setpoint.temperature_c,
                    "previous_temp": current_temp,
                    "reason": setpoint.reason,
                    "expected_savings_sek": setpoint.expected_savings_sek,
                    "valid_until": setpoint.valid_until.isoformat() if setpoint.valid_until else None,
                },
            )

            # Callback if registered
            if self._on_setpoint_callback:
                self._on_setpoint_callback(zone_id, setpoint)

        except Exception as err:
            _LOGGER.error(
                "Failed to apply setpoint to %s: %s",
                climate_entity_id,
                err
            )

    def _schedule_setpoint(
        self,
        zone_id: str,
        setpoint: SetpointCommand,
        climate_entity_id: str,
    ) -> None:
        """Schedule a setpoint for future application."""
        if not setpoint.valid_from:
            return

        # Cancel any existing scheduled setpoint for this zone
        if zone_id in self._scheduled_unsubs:
            self._scheduled_unsubs[zone_id]()

        _LOGGER.info(
            "Scheduling setpoint for zone %s at %s",
            zone_id,
            setpoint.valid_from
        )

        @callback
        def apply_scheduled_setpoint(now: datetime) -> None:
            """Apply the scheduled setpoint."""
            _LOGGER.info("Executing scheduled setpoint for zone %s", zone_id)
            self._hass.async_create_task(
                self._apply_setpoint(zone_id, setpoint, climate_entity_id)
            )
            # Remove from scheduled
            if zone_id in self._scheduled_unsubs:
                del self._scheduled_unsubs[zone_id]

        # Schedule the setpoint
        self._scheduled_unsubs[zone_id] = async_track_point_in_time(
            self._hass,
            apply_scheduled_setpoint,
            setpoint.valid_from,
        )

    def update_zones(self, zones: dict[str, dict[str, Any]]) -> None:
        """Update the zones configuration."""
        self._zones = zones

    def set_auto_control(self, zone_id: str, enabled: bool) -> None:
        """Set auto-control for a zone."""
        if zone_id in self._zones:
            self._zones[zone_id]["auto_control"] = enabled
            _LOGGER.info(
                "Auto-control for zone %s set to %s",
                zone_id,
                enabled
            )

    async def async_publish_status(
        self,
        zone_id: str,
        current_temp: float,
        setpoint_applied: bool,
        heating_active: bool,
    ) -> None:
        """Publish zone status to MQTT (optional feedback to IoT Platform)."""
        if not await mqtt.async_wait_for_mqtt_client(self._hass):
            return

        topic = f"ha/{self._installation_id}/zone/{zone_id}/status"
        payload = json.dumps({
            "current_temp_c": current_temp,
            "setpoint_applied": setpoint_applied,
            "heating_active": heating_active,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })

        try:
            await mqtt.async_publish(
                self._hass,
                topic,
                payload,
                qos=1,
                retain=False,
            )
        except Exception as err:
            _LOGGER.debug("Failed to publish status: %s", err)
