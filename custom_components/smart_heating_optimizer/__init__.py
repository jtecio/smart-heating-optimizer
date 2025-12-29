"""Smart Heating Optimizer integration for Home Assistant."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import SmartHeatingAPIClient, SmartHeatingAPIError
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
    SETPOINT_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# Note: SELECT, BUTTON, NUMBER temporarily disabled - need coordinator methods
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
]


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

    # Register services
    await async_setup_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Stop telemetry sender
    if entry.entry_id in hass.data[DOMAIN]:
        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        coordinator.stop_telemetry_sender()

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
            coordinator = entry_data.get("coordinator")
            if coordinator is None:
                _LOGGER.warning("No coordinator found for entry %s", entry_id)
                continue
            try:
                await coordinator.async_send_telemetry()
            except Exception as err:
                _LOGGER.error("Failed to send telemetry for entry %s: %s", entry_id, err)

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
        self._setpoint_unsub = None
        self._next_setpoint_poll: int = SETPOINT_POLL_INTERVAL
        self._zones: list[dict[str, Any]] = []
        self._installation: dict[str, Any] = {}
        self._dashboard: dict[str, Any] = {}

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
        """Start the telemetry sender and setpoint poller."""
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

        # Start setpoint poller
        self._start_setpoint_poller()

    def _start_setpoint_poller(self) -> None:
        """Start polling for setpoint commands."""
        if self._setpoint_unsub is not None:
            return

        @callback
        def _poll_setpoints_callback(now: datetime) -> None:
            """Poll for pending setpoints."""
            self.hass.async_create_task(self.async_poll_and_apply_setpoints())

        self._setpoint_unsub = async_track_time_interval(
            self.hass,
            _poll_setpoints_callback,
            timedelta(seconds=self._next_setpoint_poll),
        )

        # Poll immediately on startup
        self.hass.async_create_task(self.async_poll_and_apply_setpoints())

    def stop_telemetry_sender(self) -> None:
        """Stop the telemetry sender and setpoint poller."""
        if self._telemetry_unsub is not None:
            self._telemetry_unsub()
            self._telemetry_unsub = None
        if self._setpoint_unsub is not None:
            self._setpoint_unsub()
            self._setpoint_unsub = None

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
                ha_version=self.hass.config.version,
                component_version="1.0.0",
            )
            _LOGGER.debug(
                "Telemetry sent: accepted=%s, rejected=%s",
                result.get("accepted_count", 0),
                result.get("rejected_count", 0),
            )
        except SmartHeatingAPIError as err:
            _LOGGER.error("Failed to send telemetry: %s", err)

    async def async_poll_and_apply_setpoints(self) -> None:
        """Poll for pending setpoint commands and apply them to thermostats."""
        try:
            result = await self.client.get_pending_setpoints()
            commands = result.get("commands", [])
            next_poll = result.get("next_poll_seconds", 60)

            # Update polling interval if server suggests different
            if next_poll != self._next_setpoint_poll:
                self._next_setpoint_poll = next_poll
                _LOGGER.debug("Updated setpoint poll interval to %s seconds", next_poll)

            if not commands:
                _LOGGER.debug("No pending setpoint commands")
                return

            _LOGGER.info("Received %s pending setpoint command(s)", len(commands))

            for cmd in commands:
                await self._apply_setpoint_command(cmd)

        except SmartHeatingAPIError as err:
            _LOGGER.error("Failed to poll setpoints: %s", err)
        except Exception as err:
            _LOGGER.error("Unexpected error polling setpoints: %s", err)

    async def _apply_setpoint_command(self, command: dict[str, Any]) -> None:
        """Apply a single setpoint command to the thermostat."""
        command_id = command.get("command_id")
        climate_entity_id = command.get("climate_entity_id")
        target_temp = command.get("target_temp_c")
        zone_name = command.get("zone_name", "Unknown")
        reason = command.get("reason", "optimization")

        if not climate_entity_id or target_temp is None:
            _LOGGER.warning("Invalid setpoint command: missing entity or temperature")
            return

        try:
            _LOGGER.info(
                "Applying setpoint for %s: %.1f°C (%s) via %s",
                zone_name, target_temp, reason, climate_entity_id
            )

            # Call climate.set_temperature service
            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                {
                    "entity_id": climate_entity_id,
                    "temperature": target_temp,
                },
                blocking=True,
            )

            # Get the new state to confirm
            climate_state = self.hass.states.get(climate_entity_id)
            actual_temp = None
            if climate_state:
                actual_temp = climate_state.attributes.get("temperature")

            # Acknowledge success to backend
            await self.client.acknowledge_setpoint(
                command_id=command_id,
                applied=True,
                actual_temp_c=actual_temp,
            )

            _LOGGER.info(
                "Setpoint applied successfully for %s: %.1f°C",
                zone_name, target_temp
            )

        except Exception as err:
            error_msg = str(err)
            _LOGGER.error(
                "Failed to apply setpoint for %s: %s",
                zone_name, error_msg
            )

            # Acknowledge failure to backend
            try:
                await self.client.acknowledge_setpoint(
                    command_id=command_id,
                    applied=False,
                    error_message=error_msg,
                )
            except Exception as ack_err:
                _LOGGER.error("Failed to acknowledge setpoint failure: %s", ack_err)

    async def _collect_zone_telemetry(
        self,
        zone: dict[str, Any],
        outdoor_temp: float | None,
    ) -> dict[str, Any] | None:
        """Collect telemetry for a single zone.

        Returns telemetry dict if temperature is available, None otherwise.
        Gracefully handles unavailable entities.
        """
        try:
            zone_id = zone.get("id")
            temp_entity_id = zone.get("temperature_entity_id")
            climate_entity_id = zone.get("climate_entity_id")
            humidity_entity_id = zone.get("humidity_entity_id")
            power_entity_id = zone.get("power_entity_id")

            if not temp_entity_id:
                _LOGGER.debug("Zone %s has no temperature entity configured", zone_id)
                return None

            # Get temperature - this is required
            temp_state = self.hass.states.get(temp_entity_id)
            if not temp_state or temp_state.state in ("unknown", "unavailable"):
                _LOGGER.debug(
                    "Temperature entity %s is unavailable for zone %s",
                    temp_entity_id, zone_id
                )
                return None

            try:
                indoor_temp = float(temp_state.state)
            except (ValueError, TypeError) as err:
                _LOGGER.debug(
                    "Could not parse temperature from %s: %s",
                    temp_entity_id, err
                )
                return None

            # Use timezone-aware UTC datetime
            telemetry = {
                "zone_id": zone_id,
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "indoor_temp_c": indoor_temp,
            }

            # Add outdoor temp if available
            if outdoor_temp is not None:
                telemetry["outdoor_temp_c"] = outdoor_temp

            # Get humidity if available (optional)
            if humidity_entity_id:
                humidity_state = self.hass.states.get(humidity_entity_id)
                if humidity_state and humidity_state.state not in ("unknown", "unavailable"):
                    try:
                        telemetry["humidity_pct"] = float(humidity_state.state)
                    except (ValueError, TypeError):
                        pass

            # Get climate state if available (optional)
            # Climate being unavailable should NOT prevent telemetry from being sent
            if climate_entity_id:
                climate_state = self.hass.states.get(climate_entity_id)
                if climate_state and climate_state.state not in ("unknown", "unavailable"):
                    # Check if heating
                    hvac_action = climate_state.attributes.get("hvac_action")
                    if hvac_action:
                        telemetry["heating_active"] = hvac_action == "heating"

                    # Get current setpoint
                    current_temp = climate_state.attributes.get("temperature")
                    if current_temp is not None:
                        try:
                            telemetry["thermostat_setpoint_c"] = float(current_temp)
                        except (ValueError, TypeError):
                            pass

            # Get power if available (optional)
            if power_entity_id:
                power_state = self.hass.states.get(power_entity_id)
                if power_state and power_state.state not in ("unknown", "unavailable"):
                    try:
                        telemetry["heating_power_w"] = float(power_state.state)
                    except (ValueError, TypeError):
                        pass

            return telemetry

        except Exception as err:
            _LOGGER.error(
                "Unexpected error collecting telemetry for zone %s: %s",
                zone.get("id", "unknown"), err
            )
            return None
