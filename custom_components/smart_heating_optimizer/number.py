"""Number entities for Smart Heating Optimizer."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SmartHeatingCoordinator
from .api_client import SmartHeatingAPIClient, SmartHeatingAPIError
from .const import (
    DOMAIN,
    ICON_HEATING,
    ICON_VACATION,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    client = hass.data[DOMAIN][entry.entry_id]["client"]

    entities: list[NumberEntity] = []

    # Add installation-level vacation mode numbers
    entities.extend([
        VacationTargetTempNumber(coordinator, entry, client),
        VacationPreHeatHoursNumber(coordinator, entry, client),
    ])

    # Add temperature control numbers for each zone
    for zone in coordinator.zones:
        entities.extend([
            ZoneMinTempNumber(coordinator, entry, zone, client),
            ZoneMaxTempNumber(coordinator, entry, zone, client),
            ZoneTargetTempNumber(coordinator, entry, zone, client),
        ])

    async_add_entities(entities)


class ZoneTemperatureNumber(CoordinatorEntity, NumberEntity):
    """Base class for zone temperature numbers."""

    _attr_has_entity_name = True
    _attr_icon = ICON_HEATING
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        zone: dict[str, Any],
        client: SmartHeatingAPIClient,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._zone_id = str(zone.get("id"))
        self._zone_name = zone.get("name", "Zone")
        self._client = client
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
            if str(zone.get("id")) == self._zone_id:
                return zone
        return {}

    async def _update_zone_temp(self, **kwargs) -> None:
        """Update zone temperature setting via API."""
        try:
            await self._client.update_zone(zone_id=self._zone_id, **kwargs)
            await self.coordinator.async_request_refresh()
        except SmartHeatingAPIError as err:
            _LOGGER.error("Failed to update zone %s: %s", self._zone_name, err)


class ZoneMinTempNumber(ZoneTemperatureNumber):
    """Number entity for zone minimum temperature."""

    _attr_native_min_value = 5.0
    _attr_native_max_value = 25.0
    _attr_native_step = 0.5

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        zone: dict[str, Any],
        client: SmartHeatingAPIClient,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, entry, zone, client)
        self._attr_unique_id = f"{entry.entry_id}_{self._zone_id}_min_temp"
        self._attr_name = "Min Temperature"

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        zone = self._get_zone_data()
        return zone.get("min_temp_c", 16.0)

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        await self._update_zone_temp(min_temp_c=value)


class ZoneMaxTempNumber(ZoneTemperatureNumber):
    """Number entity for zone maximum temperature."""

    _attr_native_min_value = 15.0
    _attr_native_max_value = 35.0
    _attr_native_step = 0.5

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        zone: dict[str, Any],
        client: SmartHeatingAPIClient,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, entry, zone, client)
        self._attr_unique_id = f"{entry.entry_id}_{self._zone_id}_max_temp"
        self._attr_name = "Max Temperature"

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        zone = self._get_zone_data()
        return zone.get("max_temp_c", 24.0)

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        await self._update_zone_temp(max_temp_c=value)


class ZoneTargetTempNumber(ZoneTemperatureNumber):
    """Number entity for zone target temperature."""

    _attr_native_min_value = 10.0
    _attr_native_max_value = 30.0
    _attr_native_step = 0.5

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        zone: dict[str, Any],
        client: SmartHeatingAPIClient,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, entry, zone, client)
        self._attr_unique_id = f"{entry.entry_id}_{self._zone_id}_target_temp"
        self._attr_name = "Target Temperature"

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        zone = self._get_zone_data()
        return zone.get("target_temp_c", 20.0)

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        await self._update_zone_temp(target_temp_c=value)


class VacationTargetTempNumber(CoordinatorEntity, NumberEntity):
    """Number entity for vacation target temperature."""

    _attr_has_entity_name = True
    _attr_icon = ICON_VACATION
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 10.0
    _attr_native_max_value = 20.0
    _attr_native_step = 0.5

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        client: SmartHeatingAPIClient,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_vacation_target_temp"
        self._attr_name = "Semester Måltemperatur"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Smart Heating Optimizer",
            manufacturer="JTEC",
            model="Smart Heating System",
            sw_version="1.3.0",
        )

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        installation = self.coordinator.installation
        return installation.get("vacation_target_temp_c", 15.0)

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        try:
            installation = self.coordinator.installation
            await self._client.update_vacation_mode(
                enabled=installation.get("vacation_mode_enabled", False),
                start_date=installation.get("vacation_start_date"),
                end_date=installation.get("vacation_end_date"),
                target_temp_c=value,
                pre_heat_hours=installation.get("vacation_pre_heat_hours", 4),
            )
            await self.coordinator.async_request_refresh()
        except SmartHeatingAPIError as err:
            _LOGGER.error("Failed to update vacation target temp: %s", err)


class VacationPreHeatHoursNumber(CoordinatorEntity, NumberEntity):
    """Number entity for vacation pre-heat hours."""

    _attr_has_entity_name = True
    _attr_icon = ICON_VACATION
    _attr_native_unit_of_measurement = "h"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_max_value = 24
    _attr_native_step = 1

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        client: SmartHeatingAPIClient,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_vacation_pre_heat_hours"
        self._attr_name = "Semester Förvärmning"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Smart Heating Optimizer",
            manufacturer="JTEC",
            model="Smart Heating System",
            sw_version="1.3.0",
        )

    @property
    def native_value(self) -> int | None:
        """Return the current value."""
        installation = self.coordinator.installation
        return installation.get("vacation_pre_heat_hours", 4)

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        try:
            installation = self.coordinator.installation
            await self._client.update_vacation_mode(
                enabled=installation.get("vacation_mode_enabled", False),
                start_date=installation.get("vacation_start_date"),
                end_date=installation.get("vacation_end_date"),
                target_temp_c=installation.get("vacation_target_temp_c", 15.0),
                pre_heat_hours=int(value),
            )
            await self.coordinator.async_request_refresh()
        except SmartHeatingAPIError as err:
            _LOGGER.error("Failed to update vacation pre-heat hours: %s", err)
