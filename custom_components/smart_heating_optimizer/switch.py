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

    # Add installation-level switches
    entities.append(AwayModeSwitch(coordinator, entry))

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


class AwayModeSwitch(CoordinatorEntity, SwitchEntity):
    """Switch for away mode - sets all zones to minimum temperature."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:home-export-outline"

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_away_mode"
        self._attr_name = "Away Mode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Smart Heating - {coordinator.installation.get('name', 'Home')}",
            manufacturer="JTEC",
            model="Smart Heating Optimizer",
            sw_version="1.0.0",
        )

    @property
    def is_on(self) -> bool:
        """Return true if away mode is enabled."""
        return self.coordinator.is_away_mode

    @property
    def icon(self) -> str:
        """Return dynamic icon."""
        if self.is_on:
            return "mdi:home-export-outline"
        return "mdi:home"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        attrs = {
            "zones_affected": len(self.coordinator.zones),
        }
        if self.coordinator.is_away_mode:
            attrs["away_since"] = self.coordinator.away_mode_since
            attrs["saved_setpoints"] = len(self.coordinator._saved_setpoints)
        return attrs

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on away mode - set all zones to min temp."""
        await self.coordinator.async_set_away_mode(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off away mode - restore previous temperatures."""
        await self.coordinator.async_set_away_mode(False)
        self.async_write_ha_state()
