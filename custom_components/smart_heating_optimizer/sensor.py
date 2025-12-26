"""Sensor entities for Smart Heating Optimizer."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO, PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SmartHeatingCoordinator
from .const import (
    ATTR_CURRENT_PRICE,
    ATTR_IS_CHEAP_PERIOD,
    ATTR_ML_STATUS,
    ATTR_NEXT_SETPOINT,
    ATTR_NEXT_SETPOINT_REASON,
    ATTR_NEXT_SETPOINT_TIME,
    ATTR_OBSERVATIONS_NEEDED,
    DOMAIN,
    ICON_HEATING,
    ICON_ML,
    ICON_SAVINGS,
    ICON_STATUS,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[SensorEntity] = []

    # Add installation-level sensors
    entities.append(InstallationStatusSensor(coordinator, entry))
    entities.append(InstallationSavingsTodaySensor(coordinator, entry))
    entities.append(InstallationSavingsTotalSensor(coordinator, entry))

    # Add zone-level sensors
    for zone in coordinator.zones:
        entities.extend(
            [
                ZoneStatusSensor(coordinator, entry, zone),
                ZoneSavingsTodaySensor(coordinator, entry, zone),
                ZoneNextChangeSensor(coordinator, entry, zone),
                ZoneMLAccuracySensor(coordinator, entry, zone),
                ZoneObservationsSensor(coordinator, entry, zone),
            ]
        )

    async_add_entities(entities)


class SmartHeatingBaseSensor(CoordinatorEntity, SensorEntity):
    """Base sensor for Smart Heating Optimizer."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Smart Heating - {coordinator.installation.get('name', 'Home')}",
            manufacturer="JTEC",
            model="Smart Heating Optimizer",
            sw_version="1.0.0",
        )


class InstallationStatusSensor(SmartHeatingBaseSensor):
    """Sensor for installation status."""

    _attr_icon = ICON_STATUS

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_status"
        self._attr_name = "Status"

    @property
    def native_value(self) -> str:
        """Return the state."""
        return self.coordinator.installation.get("status", "unknown")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        dashboard = self.coordinator.dashboard
        return {
            "zones_optimizing": dashboard.get("zones_optimizing", 0),
            "zones_learning": dashboard.get("zones_learning", 0),
            "zones_error": dashboard.get("zones_error", 0),
            "current_price_sek": dashboard.get("current_price_sek"),
            "is_cheap_now": dashboard.get("is_cheap_now", False),
        }


class InstallationSavingsTodaySensor(SmartHeatingBaseSensor):
    """Sensor for today's savings."""

    _attr_icon = ICON_SAVINGS
    _attr_native_unit_of_measurement = "SEK"
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_savings_today"
        self._attr_name = "Savings Today"

    @property
    def native_value(self) -> float:
        """Return the state."""
        return self.coordinator.dashboard.get("today_savings_sek", 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        dashboard = self.coordinator.dashboard
        return {
            "savings_kwh": dashboard.get("today_savings_kwh", 0),
            "savings_percent": dashboard.get("today_savings_pct", 0),
        }


class InstallationSavingsTotalSensor(SmartHeatingBaseSensor):
    """Sensor for total savings."""

    _attr_icon = ICON_SAVINGS
    _attr_native_unit_of_measurement = "SEK"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_savings_total"
        self._attr_name = "Savings Total"

    @property
    def native_value(self) -> float:
        """Return the state."""
        return self.coordinator.installation.get("total_savings_all_time_sek", 0)


class ZoneBaseSensor(CoordinatorEntity, SensorEntity):
    """Base sensor for zone-specific data."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        zone: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._zone_id = zone.get("id")
        self._zone_name = zone.get("name", "Zone")
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


class ZoneStatusSensor(ZoneBaseSensor):
    """Sensor for zone status."""

    _attr_icon = ICON_STATUS

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        zone: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, zone)
        self._attr_unique_id = f"{entry.entry_id}_{self._zone_id}_status"
        self._attr_name = "Status"

    @property
    def native_value(self) -> str:
        """Return the state."""
        zone = self._get_zone_data()
        return zone.get("status", "unknown")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        zone = self._get_zone_data()
        return {
            "current_temp_c": zone.get("current_temp_c"),
            "current_setpoint_c": zone.get("current_setpoint_c"),
            "heating_active": zone.get("heating_active"),
            "auto_control_enabled": zone.get("auto_control_enabled"),
            "ml_model_accuracy": zone.get("ml_model_accuracy"),
        }


class ZoneSavingsTodaySensor(ZoneBaseSensor):
    """Sensor for zone savings today."""

    _attr_icon = ICON_SAVINGS
    _attr_native_unit_of_measurement = "SEK"
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        zone: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, zone)
        self._attr_unique_id = f"{entry.entry_id}_{self._zone_id}_savings_today"
        self._attr_name = "Savings Today"

    @property
    def native_value(self) -> float:
        """Return the state."""
        zone = self._get_zone_data()
        return zone.get("today_savings_sek", 0)


class ZoneNextChangeSensor(ZoneBaseSensor):
    """Sensor for next scheduled setpoint change."""

    _attr_icon = ICON_HEATING
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        zone: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, zone)
        self._attr_unique_id = f"{entry.entry_id}_{self._zone_id}_next_change"
        self._attr_name = "Next Change"

    @property
    def native_value(self) -> datetime | None:
        """Return the state."""
        zone = self._get_zone_data()
        next_at = zone.get("next_setpoint_at")
        if next_at:
            if isinstance(next_at, str):
                try:
                    return datetime.fromisoformat(next_at.replace("Z", "+00:00"))
                except ValueError:
                    return None
            return next_at
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        zone = self._get_zone_data()
        return {
            ATTR_NEXT_SETPOINT: zone.get("next_setpoint_c"),
            ATTR_NEXT_SETPOINT_REASON: zone.get("next_setpoint_reason"),
        }


class ZoneMLAccuracySensor(ZoneBaseSensor):
    """Sensor for ML model accuracy."""

    _attr_icon = ICON_ML
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        zone: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, zone)
        self._attr_unique_id = f"{entry.entry_id}_{self._zone_id}_ml_accuracy"
        self._attr_name = "ML Accuracy"

    @property
    def native_value(self) -> float | None:
        """Return the state."""
        zone = self._get_zone_data()
        accuracy = zone.get("ml_model_accuracy")
        if accuracy is not None:
            return round(accuracy * 100, 1)
        return None


class ZoneObservationsSensor(ZoneBaseSensor):
    """Sensor for observation count."""

    _attr_icon = ICON_ML
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        zone: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, zone)
        self._attr_unique_id = f"{entry.entry_id}_{self._zone_id}_observations"
        self._attr_name = "Observations"

    @property
    def native_value(self) -> int:
        """Return the state."""
        zone = self._get_zone_data()
        return zone.get("observation_count", 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        zone = self._get_zone_data()
        # Estimate observations needed for training (usually ~500)
        current = zone.get("observation_count", 0)
        needed = max(0, 500 - current)
        return {
            ATTR_OBSERVATIONS_NEEDED: needed,
            ATTR_ML_STATUS: zone.get("status", "unknown"),
        }
