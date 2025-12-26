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
    ATTR_PRICE_LEVEL,
    ATTR_TODAY_AVG_PRICE,
    ATTR_TODAY_MAX_PRICE,
    ATTR_TODAY_MIN_PRICE,
    DOMAIN,
    ICON_HEATING,
    ICON_ML,
    ICON_PRICE,
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

    # Add spot price sensors
    entities.append(SpotPriceCurrentSensor(coordinator, entry))
    entities.append(SpotPriceLevelSensor(coordinator, entry))

    # Add diagnostics sensor
    entities.append(ConnectionStatusSensor(coordinator, entry))

    # Add zone-level sensors
    for zone in coordinator.zones:
        entities.extend(
            [
                ZoneStatusSensor(coordinator, entry, zone),
                ZoneSavingsTodaySensor(coordinator, entry, zone),
                ZoneNextChangeSensor(coordinator, entry, zone),
                ZoneMLAccuracySensor(coordinator, entry, zone),
                ZoneObservationsSensor(coordinator, entry, zone),
                ZoneAppliedSetpointSensor(coordinator, entry, zone),
                ZoneBoostStatusSensor(coordinator, entry, zone),
                ZoneCurrentTempSensor(coordinator, entry, zone),
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


class SpotPriceCurrentSensor(SmartHeatingBaseSensor):
    """Sensor for current spot price."""

    _attr_icon = ICON_PRICE
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_spot_price"
        self._attr_name = "Spot Price"

    @property
    def native_value(self) -> float | None:
        """Return the state."""
        spot_price = self.coordinator.spot_price
        if spot_price:
            return spot_price.get("current_price_sek_kwh")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        spot_price = self.coordinator.spot_price
        if not spot_price:
            return {}
        return {
            ATTR_PRICE_LEVEL: spot_price.get("price_level"),
            ATTR_TODAY_AVG_PRICE: spot_price.get("today_avg_price_sek_kwh"),
            ATTR_TODAY_MIN_PRICE: spot_price.get("today_min_price_sek_kwh"),
            ATTR_TODAY_MAX_PRICE: spot_price.get("today_max_price_sek_kwh"),
            "next_price_sek_kwh": spot_price.get("next_price_sek_kwh"),
            "next_price_at": spot_price.get("next_price_at"),
        }


class SpotPriceLevelSensor(SmartHeatingBaseSensor):
    """Sensor for spot price level (low/normal/high)."""

    _attr_icon = ICON_PRICE

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_spot_price_level"
        self._attr_name = "Price Level"

    @property
    def native_value(self) -> str | None:
        """Return the state."""
        spot_price = self.coordinator.spot_price
        if spot_price:
            return spot_price.get("price_level", "unknown")
        return "unknown"

    @property
    def icon(self) -> str:
        """Return dynamic icon based on price level."""
        level = self.native_value
        if level == "low":
            return "mdi:arrow-down-circle"
        elif level == "high":
            return "mdi:arrow-up-circle"
        return "mdi:minus-circle"


class ZoneAppliedSetpointSensor(ZoneBaseSensor):
    """Sensor for last applied setpoint."""

    _attr_icon = ICON_HEATING
    _attr_native_unit_of_measurement = "°C"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        zone: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, zone)
        self._attr_unique_id = f"{entry.entry_id}_{self._zone_id}_applied_setpoint"
        self._attr_name = "Applied Setpoint"

    @property
    def native_value(self) -> float | None:
        """Return the state."""
        setpoint = self.coordinator.get_applied_setpoint(str(self._zone_id))
        if setpoint:
            return setpoint.get("temperature_c")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        setpoint = self.coordinator.get_applied_setpoint(str(self._zone_id))
        if not setpoint:
            return {}
        return {
            "reason": setpoint.get("reason"),
            "valid_until": setpoint.get("valid_until"),
            "expected_savings_sek": setpoint.get("expected_savings_sek"),
            "applied_at": setpoint.get("applied_at"),
        }


class ZoneBoostStatusSensor(ZoneBaseSensor):
    """Sensor for zone boost status."""

    _attr_icon = "mdi:rocket-launch"

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        zone: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, zone)
        self._attr_unique_id = f"{entry.entry_id}_{self._zone_id}_boost_status"
        self._attr_name = "Boost Status"

    @property
    def native_value(self) -> str:
        """Return the state."""
        if self.coordinator.is_boosted(str(self._zone_id)):
            return "active"
        return "inactive"

    @property
    def icon(self) -> str:
        """Return dynamic icon."""
        if self.native_value == "active":
            return "mdi:rocket-launch"
        return "mdi:rocket-launch-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        zone_id = str(self._zone_id)
        if hasattr(self.coordinator, '_boost_until'):
            boost_until = self.coordinator._boost_until.get(zone_id)
            if boost_until:
                return {
                    "boost_until": boost_until.isoformat(),
                    "is_boosted": self.coordinator.is_boosted(zone_id),
                }
        return {"is_boosted": False}


class ZoneCurrentTempSensor(ZoneBaseSensor):
    """Sensor for zone current temperature (from climate entity)."""

    _attr_icon = "mdi:thermometer"
    _attr_native_unit_of_measurement = "°C"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
        zone: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, zone)
        self._attr_unique_id = f"{entry.entry_id}_{self._zone_id}_current_temp"
        self._attr_name = "Current Temperature"

    @property
    def native_value(self) -> float | None:
        """Return the state."""
        zone = self._get_zone_data()
        return zone.get("current_temp_c")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        zone = self._get_zone_data()
        return {
            "setpoint_c": zone.get("current_setpoint_c"),
            "heating_active": zone.get("heating_active"),
            "humidity_pct": zone.get("humidity_pct"),
        }


class ConnectionStatusSensor(SmartHeatingBaseSensor):
    """Sensor for connection and diagnostics status."""

    _attr_icon = "mdi:connection"

    def __init__(
        self,
        coordinator: SmartHeatingCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_connection_status"
        self._attr_name = "Connection Status"

    @property
    def native_value(self) -> str:
        """Return the state."""
        if self.coordinator.last_update_success:
            return "connected"
        return "disconnected"

    @property
    def icon(self) -> str:
        """Return dynamic icon."""
        if self.native_value == "connected":
            return "mdi:cloud-check"
        return "mdi:cloud-off-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        installation = self.coordinator.installation
        dashboard = self.coordinator.dashboard

        # Get last update time if available
        last_update = None
        if hasattr(self.coordinator, 'last_update_success_time') and self.coordinator.last_update_success_time:
            last_update = self.coordinator.last_update_success_time.isoformat()

        attrs = {
            "last_update": last_update,
            "update_interval_seconds": 60,
            "zones_count": len(self.coordinator.zones),
            "telemetry_count": installation.get("telemetry_count", 0),
            "last_telemetry_at": installation.get("last_telemetry_at"),
            "optimization_enabled": installation.get("optimization_enabled", True),
            "optimization_mode": installation.get("optimization_mode", "balanced"),
            "away_mode": self.coordinator.is_away_mode,
        }

        # Add counts from dashboard
        if dashboard:
            attrs["zones_optimizing"] = dashboard.get("zones_optimizing", 0)
            attrs["zones_learning"] = dashboard.get("zones_learning", 0)
            attrs["zones_error"] = dashboard.get("zones_error", 0)

        return attrs
