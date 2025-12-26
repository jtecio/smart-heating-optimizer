"""Smart Heating Optimizer integration for Home Assistant."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, __version__ as HA_VERSION
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import SmartHeatingAPIClient, SmartHeatingAPIError
from .mqtt_handler import SmartHeatingMQTTHandler, SetpointCommand
from .const import (
    CONF_API_KEY,
    CONF_API_URL,
    CONF_CUSTOMER_ID,
    CONF_INSTALLATION_ID,
    CONF_OUTDOOR_TEMP_ENTITY,
    DOMAIN,
    PLATFORMS,
    SCAN_INTERVAL,
    TELEMETRY_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Heating Optimizer from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Create API client
    session = async_get_clientsession(hass)
    client = SmartHeatingAPIClient(
        api_url=entry.data[CONF_API_URL],
        api_key=entry.data[CONF_API_KEY],
        customer_id=entry.data[CONF_CUSTOMER_ID],
        session=session,
    )
    client.installation_id = entry.data.get(CONF_INSTALLATION_ID)

    # Create coordinator
    coordinator = SmartHeatingCoordinator(
        hass,
        client,
        entry,
    )

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
    }

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start telemetry sender
    coordinator.start_telemetry_sender()

    # Start MQTT handler for receiving setpoints
    await coordinator.async_start_mqtt_handler()

    # Register services
    await async_setup_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Stop telemetry sender and MQTT handler
    if entry.entry_id in hass.data[DOMAIN]:
        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        coordinator.stop_telemetry_sender()
        await coordinator.async_stop_mqtt_handler()

    # Unload platforms
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for the integration."""

    async def handle_trigger_optimization(call) -> None:
        """Handle the trigger optimization service call."""
        # Get all entries
        for entry_id, entry_data in hass.data[DOMAIN].items():
            client = entry_data["client"]
            try:
                await client.trigger_optimization(
                    force=call.data.get("force", False),
                    target_date=call.data.get("target_date"),
                )
            except SmartHeatingAPIError as err:
                _LOGGER.error("Failed to trigger optimization: %s", err)

    async def handle_send_telemetry(call) -> None:
        """Handle manual telemetry send."""
        for entry_id, entry_data in hass.data[DOMAIN].items():
            coordinator = entry_data["coordinator"]
            await coordinator.async_send_telemetry()

    # Register services if not already registered
    if not hass.services.has_service(DOMAIN, "trigger_optimization"):
        hass.services.async_register(
            DOMAIN,
            "trigger_optimization",
            handle_trigger_optimization,
        )

    if not hass.services.has_service(DOMAIN, "send_telemetry"):
        hass.services.async_register(
            DOMAIN,
            "send_telemetry",
            handle_send_telemetry,
        )


class SmartHeatingCoordinator(DataUpdateCoordinator):
    """Coordinator for Smart Heating Optimizer."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SmartHeatingAPIClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL),
        )
        self.client = client
        self.entry = entry
        self._telemetry_unsub = None
        self._zones: list[dict[str, Any]] = []
        self._installation: dict[str, Any] = {}
        self._dashboard: dict[str, Any] = {}
        self._mqtt_handler: SmartHeatingMQTTHandler | None = None

    @property
    def zones(self) -> list[dict[str, Any]]:
        """Return the zones."""
        return self._zones

    @property
    def installation(self) -> dict[str, Any]:
        """Return the installation data."""
        return self._installation

    @property
    def dashboard(self) -> dict[str, Any]:
        """Return the dashboard data."""
        return self._dashboard

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API."""
        try:
            # Fetch installation, zones, and dashboard in parallel
            installation_task = self.client.get_installation()
            zones_task = self.client.get_zones()
            dashboard_task = self.client.get_dashboard()

            self._installation, self._zones, self._dashboard = await asyncio.gather(
                installation_task,
                zones_task,
                dashboard_task,
            )

            return {
                "installation": self._installation,
                "zones": self._zones,
                "dashboard": self._dashboard,
            }

        except SmartHeatingAPIError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    def start_telemetry_sender(self) -> None:
        """Start the telemetry sender."""
        if self._telemetry_unsub is not None:
            return

        @callback
        def _send_telemetry_callback(now: datetime) -> None:
            """Send telemetry callback."""
            self.hass.async_create_task(self.async_send_telemetry())

        self._telemetry_unsub = async_track_time_interval(
            self.hass,
            _send_telemetry_callback,
            timedelta(seconds=TELEMETRY_INTERVAL),
        )

        # Send initial telemetry
        self.hass.async_create_task(self.async_send_telemetry())

    def stop_telemetry_sender(self) -> None:
        """Stop the telemetry sender."""
        if self._telemetry_unsub is not None:
            self._telemetry_unsub()
            self._telemetry_unsub = None

    async def async_start_mqtt_handler(self) -> None:
        """Start the MQTT handler for receiving setpoints."""
        installation_id = self.entry.data.get(CONF_INSTALLATION_ID)
        if not installation_id:
            _LOGGER.warning("No installation ID, cannot start MQTT handler")
            return

        # Build zones dict for MQTT handler
        zones_dict = {}
        for zone in self._zones:
            zone_id = zone.get("id")
            if zone_id:
                zones_dict[zone_id] = {
                    "climate_entity_id": zone.get("climate_entity_id"),
                    "auto_control": zone.get("auto_control_enabled", True),
                }

        self._mqtt_handler = SmartHeatingMQTTHandler(
            hass=self.hass,
            installation_id=installation_id,
            zones=zones_dict,
            on_setpoint_callback=self._on_setpoint_applied,
        )

        if await self._mqtt_handler.async_subscribe():
            _LOGGER.info("MQTT handler started for installation %s", installation_id)
        else:
            _LOGGER.warning("MQTT handler could not subscribe (MQTT not available)")

    async def async_stop_mqtt_handler(self) -> None:
        """Stop the MQTT handler."""
        if self._mqtt_handler:
            await self._mqtt_handler.async_unsubscribe()
            self._mqtt_handler = None
            _LOGGER.info("MQTT handler stopped")

    def _on_setpoint_applied(self, zone_id: str, setpoint: SetpointCommand) -> None:
        """Callback when a setpoint is applied."""
        _LOGGER.info(
            "Setpoint applied for zone %s: %.1f°C (reason: %s)",
            zone_id,
            setpoint.temperature_c,
            setpoint.reason,
        )

    @property
    def mqtt_handler(self) -> SmartHeatingMQTTHandler | None:
        """Return the MQTT handler."""
        return self._mqtt_handler

    def get_pending_setpoint(self, zone_id: str) -> SetpointCommand | None:
        """Get pending setpoint for a zone."""
        if self._mqtt_handler:
            return self._mqtt_handler.get_pending_setpoint(zone_id)
        return None

    def get_applied_setpoint(self, zone_id: str) -> SetpointCommand | None:
        """Get last applied setpoint for a zone."""
        if self._mqtt_handler:
            return self._mqtt_handler.get_applied_setpoint(zone_id)
        return None

    async def async_send_telemetry(self) -> None:
        """Send telemetry data to the IoT Platform."""
        if not self._zones:
            _LOGGER.debug("No zones configured, skipping telemetry")
            return

        # Get outdoor temperature if configured
        outdoor_temp = None
        outdoor_entity_id = self.entry.data.get(CONF_OUTDOOR_TEMP_ENTITY)
        if outdoor_entity_id:
            outdoor_state = self.hass.states.get(outdoor_entity_id)
            if outdoor_state and outdoor_state.state not in ("unknown", "unavailable"):
                try:
                    outdoor_temp = float(outdoor_state.state)
                except ValueError:
                    pass

        # Collect telemetry for each zone
        zone_telemetry = []
        for zone in self._zones:
            telemetry = await self._collect_zone_telemetry(zone, outdoor_temp)
            if telemetry:
                zone_telemetry.append(telemetry)

        if not zone_telemetry:
            _LOGGER.debug("No telemetry collected, skipping send")
            return

        try:
            result = await self.client.send_telemetry(
                zones=zone_telemetry,
                outdoor_temp=outdoor_temp,
                ha_version=HA_VERSION,
                component_version="1.0.0",
            )
            _LOGGER.debug(
                "Telemetry sent: accepted=%s, rejected=%s",
                result.get("accepted_count", 0),
                result.get("rejected_count", 0),
            )
        except SmartHeatingAPIError as err:
            _LOGGER.error("Failed to send telemetry: %s", err)

    async def _collect_zone_telemetry(
        self,
        zone: dict[str, Any],
        outdoor_temp: float | None,
    ) -> dict[str, Any] | None:
        """Collect telemetry for a single zone."""
        zone_id = zone.get("id")
        temp_entity_id = zone.get("temperature_entity_id")
        climate_entity_id = zone.get("climate_entity_id")
        humidity_entity_id = zone.get("humidity_entity_id")
        power_entity_id = zone.get("power_entity_id")

        if not temp_entity_id:
            return None

        # Get temperature
        temp_state = self.hass.states.get(temp_entity_id)
        if not temp_state or temp_state.state in ("unknown", "unavailable"):
            return None

        try:
            indoor_temp = float(temp_state.state)
        except ValueError:
            return None

        telemetry = {
            "zone_id": zone_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "indoor_temp_c": indoor_temp,
        }

        # Add outdoor temp if available
        if outdoor_temp is not None:
            telemetry["outdoor_temp_c"] = outdoor_temp

        # Get humidity if available
        if humidity_entity_id:
            humidity_state = self.hass.states.get(humidity_entity_id)
            if humidity_state and humidity_state.state not in ("unknown", "unavailable"):
                try:
                    telemetry["humidity_pct"] = float(humidity_state.state)
                except ValueError:
                    pass

        # Get climate state
        if climate_entity_id:
            climate_state = self.hass.states.get(climate_entity_id)
            if climate_state:
                # Check if heating
                hvac_action = climate_state.attributes.get("hvac_action")
                if hvac_action:
                    telemetry["heating_active"] = hvac_action == "heating"

                # Get current setpoint
                current_temp = climate_state.attributes.get("temperature")
                if current_temp is not None:
                    try:
                        telemetry["thermostat_setpoint_c"] = float(current_temp)
                    except ValueError:
                        pass

        # Get power if available
        if power_entity_id:
            power_state = self.hass.states.get(power_entity_id)
            if power_state and power_state.state not in ("unknown", "unavailable"):
                try:
                    telemetry["heating_power_w"] = float(power_state.state)
                except ValueError:
                    pass

        return telemetry
