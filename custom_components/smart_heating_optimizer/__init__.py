"""Smart Heating Optimizer integration for Home Assistant."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
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
)

_LOGGER = logging.getLogger(__name__)


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

    # Start telemetry sender (also handles setpoints from response)
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

    async def handle_boost(call) -> None:
        """Handle boost service - temporarily increase temperature."""
        zone_id = call.data.get("zone_id")
        duration_minutes = call.data.get("duration", 120)
        temp_increase = call.data.get("increase", 2.0)

        for entry_id, entry_data in hass.data[DOMAIN].items():
            coordinator = entry_data["coordinator"]
            await coordinator.async_boost_zone(zone_id, duration_minutes, temp_increase)

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

    if not hass.services.has_service(DOMAIN, "boost"):
        hass.services.async_register(
            DOMAIN,
            "boost",
            handle_boost,
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

        # Spot price info (updated with each telemetry response)
        self._spot_price: dict[str, Any] | None = None

        # Applied setpoints per zone
        self._applied_setpoints: dict[str, dict[str, Any]] = {}

        # Boost state per zone
        self._boost_until: dict[str, datetime] = {}

        # Away mode state
        self._away_mode: bool = False
        self._away_mode_since: datetime | None = None
        self._saved_setpoints: dict[str, float] = {}

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

    @property
    def spot_price(self) -> dict[str, Any] | None:
        """Return the current spot price info."""
        return self._spot_price

    @property
    def is_away_mode(self) -> bool:
        """Return true if away mode is enabled."""
        return self._away_mode

    @property
    def away_mode_since(self) -> str | None:
        """Return when away mode was enabled."""
        if self._away_mode_since:
            return self._away_mode_since.isoformat()
        return None

    def get_applied_setpoint(self, zone_id: str) -> dict[str, Any] | None:
        """Get last applied setpoint for a zone."""
        return self._applied_setpoints.get(zone_id)

    def is_boosted(self, zone_id: str) -> bool:
        """Check if a zone is currently boosted."""
        if zone_id not in self._boost_until:
            return False
        return datetime.utcnow() < self._boost_until[zone_id]

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
                "spot_price": self._spot_price,
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

    async def async_send_telemetry(self) -> None:
        """Send telemetry data to the IoT Platform and process response."""
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

            # Update spot price from response
            if "spot_price" in result and result["spot_price"]:
                self._spot_price = result["spot_price"]

            # Process pending setpoints from response
            pending_setpoints = result.get("pending_setpoints", [])
            for setpoint in pending_setpoints:
                await self._apply_setpoint(setpoint)

        except SmartHeatingAPIError as err:
            _LOGGER.error("Failed to send telemetry: %s", err)

    async def _apply_setpoint(self, setpoint: dict[str, Any]) -> None:
        """Apply a setpoint to a zone's climate entity."""
        zone_id = setpoint.get("zone_id")
        if not zone_id:
            return

        # Find the zone
        zone = None
        for z in self._zones:
            if str(z.get("id")) == str(zone_id):
                zone = z
                break

        if not zone:
            _LOGGER.warning("Zone %s not found for setpoint", zone_id)
            return

        # Check if zone has auto-control enabled
        if not zone.get("auto_control_enabled", True):
            _LOGGER.debug("Auto-control disabled for zone %s, skipping setpoint", zone_id)
            return

        # Check if zone is boosted (don't override boost)
        if self.is_boosted(str(zone_id)):
            _LOGGER.debug("Zone %s is boosted, skipping setpoint", zone_id)
            return

        climate_entity_id = zone.get("climate_entity_id")
        if not climate_entity_id:
            _LOGGER.warning("No climate entity for zone %s", zone_id)
            return

        temperature = setpoint.get("temperature_c")
        if temperature is None:
            return

        # Check if climate entity exists
        state = self.hass.states.get(climate_entity_id)
        if not state:
            _LOGGER.error("Climate entity not found: %s", climate_entity_id)
            return

        current_temp = state.attributes.get("temperature")

        _LOGGER.info(
            "Applying setpoint to %s: %s -> %s (reason: %s)",
            climate_entity_id,
            current_temp,
            temperature,
            setpoint.get("reason", "optimization"),
        )

        try:
            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                {
                    "entity_id": climate_entity_id,
                    "temperature": temperature,
                },
                blocking=True,
            )

            # Store applied setpoint
            self._applied_setpoints[str(zone_id)] = setpoint

            # Fire event for tracking
            self.hass.bus.async_fire(
                f"{DOMAIN}_setpoint_applied",
                {
                    "zone_id": zone_id,
                    "zone_name": setpoint.get("zone_name", zone.get("name")),
                    "climate_entity_id": climate_entity_id,
                    "temperature_c": temperature,
                    "previous_temp": current_temp,
                    "reason": setpoint.get("reason"),
                    "expected_savings_sek": setpoint.get("expected_savings_sek"),
                    "valid_until": setpoint.get("valid_until"),
                },
            )

        except Exception as err:
            _LOGGER.error("Failed to apply setpoint to %s: %s", climate_entity_id, err)

    async def async_boost_zone(
        self,
        zone_id: str | None,
        duration_minutes: int = 120,
        temp_increase: float = 2.0,
    ) -> None:
        """Boost a zone's temperature temporarily."""
        zones_to_boost = []

        if zone_id:
            # Boost specific zone
            for z in self._zones:
                if str(z.get("id")) == str(zone_id):
                    zones_to_boost.append(z)
                    break
        else:
            # Boost all zones
            zones_to_boost = self._zones

        for zone in zones_to_boost:
            zid = str(zone.get("id"))
            climate_entity_id = zone.get("climate_entity_id")
            if not climate_entity_id:
                continue

            state = self.hass.states.get(climate_entity_id)
            if not state:
                continue

            current_temp = state.attributes.get("temperature", 20)
            boost_temp = current_temp + temp_increase

            # Apply boost
            try:
                await self.hass.services.async_call(
                    "climate",
                    "set_temperature",
                    {
                        "entity_id": climate_entity_id,
                        "temperature": boost_temp,
                    },
                    blocking=True,
                )

                # Track boost end time
                self._boost_until[zid] = datetime.utcnow() + timedelta(minutes=duration_minutes)

                _LOGGER.info(
                    "Boosted zone %s to %.1f°C for %d minutes",
                    zone.get("name"),
                    boost_temp,
                    duration_minutes,
                )

            except Exception as err:
                _LOGGER.error("Failed to boost zone %s: %s", zone.get("name"), err)

    async def async_set_away_mode(self, enabled: bool) -> None:
        """Enable or disable away mode."""
        if enabled and not self._away_mode:
            # Enabling away mode - save current setpoints and set to min
            self._away_mode = True
            self._away_mode_since = datetime.utcnow()
            self._saved_setpoints = {}

            for zone in self._zones:
                climate_entity_id = zone.get("climate_entity_id")
                if not climate_entity_id:
                    continue

                # Get current setpoint to save
                state = self.hass.states.get(climate_entity_id)
                if state:
                    current_temp = state.attributes.get("temperature")
                    if current_temp is not None:
                        self._saved_setpoints[str(zone.get("id"))] = float(current_temp)

                # Set to minimum temperature
                min_temp = zone.get("min_temp_c", 16.0)
                try:
                    await self.hass.services.async_call(
                        "climate",
                        "set_temperature",
                        {
                            "entity_id": climate_entity_id,
                            "temperature": min_temp,
                        },
                        blocking=True,
                    )
                    _LOGGER.info(
                        "Away mode: Set %s to %.1f°C (was %.1f°C)",
                        zone.get("name"),
                        min_temp,
                        self._saved_setpoints.get(str(zone.get("id")), 0),
                    )
                except Exception as err:
                    _LOGGER.error("Failed to set away temp for %s: %s", zone.get("name"), err)

            _LOGGER.info("Away mode enabled for %d zones", len(self._zones))

        elif not enabled and self._away_mode:
            # Disabling away mode - restore saved setpoints
            self._away_mode = False

            for zone in self._zones:
                zone_id = str(zone.get("id"))
                climate_entity_id = zone.get("climate_entity_id")
                if not climate_entity_id:
                    continue

                # Restore saved setpoint or use target temp
                restore_temp = self._saved_setpoints.get(
                    zone_id,
                    zone.get("target_temp_c", 20.0)
                )

                try:
                    await self.hass.services.async_call(
                        "climate",
                        "set_temperature",
                        {
                            "entity_id": climate_entity_id,
                            "temperature": restore_temp,
                        },
                        blocking=True,
                    )
                    _LOGGER.info(
                        "Away mode off: Restored %s to %.1f°C",
                        zone.get("name"),
                        restore_temp,
                    )
                except Exception as err:
                    _LOGGER.error("Failed to restore temp for %s: %s", zone.get("name"), err)

            self._away_mode_since = None
            self._saved_setpoints = {}
            _LOGGER.info("Away mode disabled, temperatures restored")

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
