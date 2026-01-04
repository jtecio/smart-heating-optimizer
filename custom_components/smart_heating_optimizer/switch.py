"""Switch entities for Smart Heating Optimizer."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SmartHeatingCoordinator
from .api_client import SmartHeatingAPIClient, SmartHeatingAPIError
from .const import (
    CONF_API_KEY,
    CONF_API_URL,
    CONF_CUSTOMER_ID,
    DOMAIN,
    ICON_AUTO,
    ICON_VACATION,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switches from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    client = hass.data[DOMAIN][entry.entry_id]["client"]

    entities: list[SwitchEntity] = []

    # Add installation-level vacation mode switch
    entities.append(VacationModeSwitch(coordinator, entry, client))

    # Add auto-control switch for each zone
    for zone in coordinator.zones:
        entities.append(ZoneAutoControlSwitch(coordinator, entry, zone, client))

    async_add_entities(entities)


class ZoneAutoControlSwitch(CoordinatorEntity, SwitchEntity):
    """Switch for zone auto-control."""

    _attr_has_entity_name = True
    _attr_icon = ICON_AUTO

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        zone: dict[str, Any],
        client: SmartHeatingAPIClient,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._zone_id = zone.get("id")
        self._zone_name = zone.get("name", "Zone")
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_{self._zone_id}_auto_control"
        self._attr_name = "Auto Control"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{self._zone_id}")},
            name=f"Smart Heating - {self._zone_name}",
            manufacturer="JTEC",
            model="Smart Heating Zone",
            sw_version="1.0.0",
            via_device=(DOMAIN, entry.entry_id),
        )

    def _get_zone_data(self) -> dict[str, Any]:
        """Get current zone data."""
        for zone in self.coordinator.zones:
            if zone.get("id") == self._zone_id:
                return zone
        return {}

    @property
    def is_on(self) -> bool:
        """Return true if auto-control is enabled."""
        zone = self._get_zone_data()
        return zone.get("auto_control_enabled", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on auto-control."""
        await self._set_auto_control(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off auto-control."""
        await self._set_auto_control(False)

    async def _set_auto_control(self, enabled: bool) -> None:
        """Set auto-control state."""
        try:
            await self._client.update_zone(
                zone_id=self._zone_id,
                auto_control_enabled=enabled,
            )
            # Refresh coordinator data
            await self.coordinator.async_request_refresh()
        except SmartHeatingAPIError as err:
            _LOGGER.error("Failed to update auto-control: %s", err)


class VacationModeSwitch(CoordinatorEntity, SwitchEntity):
    """Switch for vacation mode (installation-level)."""

    _attr_has_entity_name = True
    _attr_icon = ICON_VACATION

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        client: SmartHeatingAPIClient,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_vacation_mode"
        self._attr_name = "Semesterläge"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Smart Heating Optimizer",
            manufacturer="JTEC",
            model="Smart Heating System",
            sw_version="1.3.0",
        )

    @property
    def is_on(self) -> bool:
        """Return true if vacation mode is enabled."""
        installation = self.coordinator.installation
        return installation.get("vacation_mode_enabled", False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        installation = self.coordinator.installation
        return {
            "start_date": installation.get("vacation_start_date"),
            "end_date": installation.get("vacation_end_date"),
            "target_temp_c": installation.get("vacation_target_temp_c", 15.0),
            "pre_heat_hours": installation.get("vacation_pre_heat_hours", 4),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on vacation mode."""
        await self._set_vacation_mode(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off vacation mode."""
        await self._set_vacation_mode(False)

    async def _set_vacation_mode(self, enabled: bool) -> None:
        """Set vacation mode state.

        When enabling, uses existing dates if set, otherwise defaults.
        """
        try:
            installation = self.coordinator.installation
            await self._client.update_vacation_mode(
                enabled=enabled,
                start_date=installation.get("vacation_start_date"),
                end_date=installation.get("vacation_end_date"),
                target_temp_c=installation.get("vacation_target_temp_c", 15.0),
                pre_heat_hours=installation.get("vacation_pre_heat_hours", 4),
            )
            # Refresh coordinator data
            await self.coordinator.async_request_refresh()
        except SmartHeatingAPIError as err:
            _LOGGER.error("Failed to update vacation mode: %s", err)
