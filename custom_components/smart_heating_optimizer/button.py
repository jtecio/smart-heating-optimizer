"""Button entities for Smart Heating Optimizer."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SmartHeatingCoordinator
from .const import (
    ATTR_BOOST_UNTIL,
    DOMAIN,
    ICON_BOOST,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[ButtonEntity] = []

    # Add boost button for each zone
    for zone in coordinator.zones:
        entities.append(ZoneBoostButton(coordinator, entry, zone))

    # Add a global boost all button
    entities.append(BoostAllButton(coordinator, entry))

    async_add_entities(entities)


class ZoneBoostButton(CoordinatorEntity, ButtonEntity):
    """Button to boost a specific zone."""

    _attr_has_entity_name = True
    _attr_icon = ICON_BOOST

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        zone: dict[str, Any],
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._entry = entry
        self._zone_id = str(zone.get("id"))
        self._zone_name = zone.get("name", "Zone")
        self._attr_unique_id = f"{entry.entry_id}_{self._zone_id}_boost"
        self._attr_name = "Boost"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{self._zone_id}")},
            name=f"Smart Heating - {self._zone_name}",
            manufacturer="JTEC",
            model="Smart Heating Zone",
            sw_version="1.0.0",
            via_device=(DOMAIN, entry.entry_id),
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.info("Boost button pressed for zone: %s", self._zone_name)
        await self.coordinator.async_boost_zone(
            zone_id=self._zone_id,
            duration_minutes=120,
            temp_increase=2.0,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        is_boosted = self.coordinator.is_boosted(self._zone_id)
        attrs = {
            "is_boosted": is_boosted,
        }
        if is_boosted and hasattr(self.coordinator, '_boost_until'):
            boost_until = self.coordinator._boost_until.get(self._zone_id)
            if boost_until:
                attrs[ATTR_BOOST_UNTIL] = boost_until.isoformat()
        return attrs


class BoostAllButton(CoordinatorEntity, ButtonEntity):
    """Button to boost all zones."""

    _attr_has_entity_name = True
    _attr_icon = ICON_BOOST

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_boost_all"
        self._attr_name = "Boost All Zones"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Smart Heating - {coordinator.installation.get('name', 'Home')}",
            manufacturer="JTEC",
            model="Smart Heating Optimizer",
            sw_version="1.0.0",
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.info("Boost All button pressed")
        await self.coordinator.async_boost_zone(
            zone_id=None,  # None means all zones
            duration_minutes=120,
            temp_increase=2.0,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        boosted_zones = []
        for zone in self.coordinator.zones:
            zone_id = str(zone.get("id"))
            if self.coordinator.is_boosted(zone_id):
                boosted_zones.append(zone.get("name", zone_id))
        return {
            "boosted_zones": boosted_zones,
            "boosted_count": len(boosted_zones),
        }
