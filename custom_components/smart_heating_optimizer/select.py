"""Select entities for Smart Heating Optimizer."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SmartHeatingCoordinator
from .api_client import SmartHeatingAPIClient, SmartHeatingAPIError
from .const import (
    DOMAIN,
    ICON_MODE,
    MODE_BALANCED,
    MODE_COMFORT,
    MODE_ECONOMY,
)

_LOGGER = logging.getLogger(__name__)

# Mode options with display names
OPTIMIZATION_MODES = {
    MODE_ECONOMY: "Economy",
    MODE_BALANCED: "Balanced",
    MODE_COMFORT: "Comfort",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    client = hass.data[DOMAIN][entry.entry_id]["client"]

    entities: list[SelectEntity] = []

    # Add installation-level optimization mode selector
    entities.append(OptimizationModeSelect(coordinator, entry, client))

    async_add_entities(entities)


class OptimizationModeSelect(CoordinatorEntity, SelectEntity):
    """Select entity for optimization mode."""

    _attr_has_entity_name = True
    _attr_icon = ICON_MODE

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        client: SmartHeatingAPIClient,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_optimization_mode"
        self._attr_name = "Optimization Mode"
        self._attr_options = list(OPTIMIZATION_MODES.values())
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Smart Heating - {coordinator.installation.get('name', 'Home')}",
            manufacturer="JTEC",
            model="Smart Heating Optimizer",
            sw_version="1.0.0",
        )

    @property
    def current_option(self) -> str | None:
        """Return the current selected option."""
        mode = self.coordinator.installation.get("optimization_mode", MODE_BALANCED)
        return OPTIMIZATION_MODES.get(mode, "Balanced")

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        # Convert display name back to mode key
        mode_key = None
        for key, display_name in OPTIMIZATION_MODES.items():
            if display_name == option:
                mode_key = key
                break

        if mode_key is None:
            _LOGGER.error("Unknown optimization mode: %s", option)
            return

        try:
            await self._client.update_installation(optimization_mode=mode_key)
            _LOGGER.info("Set optimization mode to: %s", mode_key)
            await self.coordinator.async_request_refresh()
        except SmartHeatingAPIError as err:
            _LOGGER.error("Failed to set optimization mode: %s", err)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        mode = self.coordinator.installation.get("optimization_mode", MODE_BALANCED)
        descriptions = {
            MODE_ECONOMY: "Maximum energy savings, lower comfort during high prices",
            MODE_BALANCED: "Balance between savings and comfort",
            MODE_COMFORT: "Prioritize comfort, minimize temperature changes",
        }
        return {
            "mode_key": mode,
            "description": descriptions.get(mode, ""),
        }
