"""Config flow for Smart Heating Optimizer integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import CONF_NAME, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import (
    SmartHeatingAPIClient,
    SmartHeatingAPIError,
    SmartHeatingAuthError,
    SmartHeatingConnectionError,
)
from .const import (
    CONF_API_KEY,
    CONF_API_URL,
    CONF_AUTO_CONTROL,
    CONF_CLIMATE_ENTITY,
    CONF_CUSTOMER_ID,
    CONF_HEATING_TYPE,
    CONF_HUMIDITY_ENTITY,
    CONF_INSTALLATION_ID,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_POWER_ENTITY,
    CONF_PRICE_AREA,
    CONF_RETURN_TEMP_ENTITY,
    CONF_SUPPLY_TEMP_ENTITY,
    CONF_TARGET_TEMP,
    CONF_TEMPERATURE_ENTITY,
    CONF_VALVE_ENTITY,
    CONF_ZONE_NAME,
    DEFAULT_API_URL,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_PRICE_AREA,
    DEFAULT_TARGET_TEMP,
    DOMAIN,
    HEATING_TYPE_ELECTRIC,
    HEATING_TYPE_HYDRONIC,
    HEATING_TYPE_MIXED,
    HEATING_TYPE_UNKNOWN,
    HEATING_TYPES,
    PRICE_AREAS,
)

_LOGGER = logging.getLogger(__name__)


async def validate_api_connection(
    hass: HomeAssistant,
    api_url: str,
    api_key: str,
    customer_id: str,
) -> dict[str, Any]:
    """Validate the API connection."""
    session = async_get_clientsession(hass)
    client = SmartHeatingAPIClient(
        api_url=api_url,
        api_key=api_key,
        customer_id=customer_id,
        session=session,
    )

    try:
        # Try to get existing installation
        installation = await client.get_installation()
        return {
            "installation_id": installation.get("id"),
            "name": installation.get("name"),
            "existing": True,
        }
    except SmartHeatingAPIError as err:
        if err.status_code == 404:
            # No installation exists yet - that's OK
            return {"existing": False}
        raise


class SmartHeatingConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Heating Optimizer."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._api_url: str = DEFAULT_API_URL
        self._api_key: str = ""
        self._customer_id: str = ""
        self._installation_name: str = ""
        self._installation_id: str | None = None
        self._price_area: str = DEFAULT_PRICE_AREA
        self._outdoor_temp_entity: str | None = None
        self._zones: list[dict[str, Any]] = []

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the initial step - API connection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._api_url = user_input.get(CONF_API_URL, DEFAULT_API_URL)
            self._api_key = user_input[CONF_API_KEY]
            self._customer_id = user_input[CONF_CUSTOMER_ID]

            try:
                result = await validate_api_connection(
                    self.hass,
                    self._api_url,
                    self._api_key,
                    self._customer_id,
                )

                if result.get("existing"):
                    # Installation already exists
                    self._installation_id = result["installation_id"]
                    self._installation_name = result["name"]
                    return await self.async_step_existing()

                # New installation - go to setup step
                return await self.async_step_setup()

            except SmartHeatingAuthError:
                errors["base"] = "invalid_auth"
            except SmartHeatingConnectionError:
                errors["base"] = "cannot_connect"
            except SmartHeatingAPIError:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_URL, default=DEFAULT_API_URL): str,
                    vol.Required(CONF_API_KEY): str,
                    vol.Required(CONF_CUSTOMER_ID): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "api_url": DEFAULT_API_URL,
            },
        )

    async def async_step_existing(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle existing installation - just link to it."""
        if user_input is not None:
            # User confirmed to link to existing installation
            return self.async_create_entry(
                title=self._installation_name,
                data={
                    CONF_API_URL: self._api_url,
                    CONF_API_KEY: self._api_key,
                    CONF_CUSTOMER_ID: self._customer_id,
                    CONF_INSTALLATION_ID: self._installation_id,
                },
            )

        return self.async_show_form(
            step_id="existing",
            description_placeholders={
                "name": self._installation_name,
            },
        )

    async def async_step_setup(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle installation setup - name and settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._installation_name = user_input[CONF_NAME]
            self._price_area = user_input.get(CONF_PRICE_AREA, DEFAULT_PRICE_AREA)
            self._outdoor_temp_entity = user_input.get(CONF_OUTDOOR_TEMP_ENTITY)

            # Try to register the installation
            session = async_get_clientsession(self.hass)
            client = SmartHeatingAPIClient(
                api_url=self._api_url,
                api_key=self._api_key,
                customer_id=self._customer_id,
                session=session,
            )

            try:
                # Get HA location for lat/lon
                latitude = self.hass.config.latitude
                longitude = self.hass.config.longitude
                timezone = str(self.hass.config.time_zone)

                result = await client.register_installation(
                    name=self._installation_name,
                    ha_version=self.hass.config.version,
                    price_area=self._price_area,
                    outdoor_temp_entity_id=self._outdoor_temp_entity,
                    latitude=latitude,
                    longitude=longitude,
                    timezone=timezone,
                )

                self._installation_id = result["installation_id"]

                # Ask user if they want to add zones now
                return await self.async_step_add_zone_prompt()

            except SmartHeatingAPIError as err:
                _LOGGER.error("Failed to register installation: %s", err)
                errors["base"] = "registration_failed"

        # Build temperature sensor selector
        temp_entities = [
            state.entity_id
            for state in self.hass.states.async_all(SENSOR_DOMAIN)
            if state.attributes.get("unit_of_measurement")
            in (UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT, "°C", "°F")
        ]

        return self.async_show_form(
            step_id="setup",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    vol.Required(CONF_PRICE_AREA, default=DEFAULT_PRICE_AREA): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=PRICE_AREAS,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(CONF_OUTDOOR_TEMP_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=SENSOR_DOMAIN,
                            device_class="temperature",
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_add_zone_prompt(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Ask if user wants to add a zone."""
        if user_input is not None:
            if user_input.get("add_zone"):
                return await self.async_step_zone()

            # User chose not to add zones - complete setup
            return self.async_create_entry(
                title=self._installation_name,
                data={
                    CONF_API_URL: self._api_url,
                    CONF_API_KEY: self._api_key,
                    CONF_CUSTOMER_ID: self._customer_id,
                    CONF_INSTALLATION_ID: self._installation_id,
                    CONF_PRICE_AREA: self._price_area,
                    CONF_OUTDOOR_TEMP_ENTITY: self._outdoor_temp_entity,
                },
            )

        return self.async_show_form(
            step_id="add_zone_prompt",
            data_schema=vol.Schema(
                {
                    vol.Required("add_zone", default=True): bool,
                }
            ),
            description_placeholders={
                "zone_count": str(len(self._zones)),
            },
        )

    async def async_step_zone(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle zone creation."""
        errors: dict[str, str] = {}

        if user_input is not None:
            zone_name = user_input[CONF_ZONE_NAME]
            heating_type = user_input.get(CONF_HEATING_TYPE, HEATING_TYPE_UNKNOWN)
            temp_entity = user_input[CONF_TEMPERATURE_ENTITY]
            climate_entity = user_input[CONF_CLIMATE_ENTITY]
            humidity_entity = user_input.get(CONF_HUMIDITY_ENTITY)
            power_entity = user_input.get(CONF_POWER_ENTITY)
            valve_entity = user_input.get(CONF_VALVE_ENTITY)
            supply_temp_entity = user_input.get(CONF_SUPPLY_TEMP_ENTITY)
            return_temp_entity = user_input.get(CONF_RETURN_TEMP_ENTITY)
            min_temp = user_input.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)
            max_temp = user_input.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)
            target_temp = user_input.get(CONF_TARGET_TEMP, DEFAULT_TARGET_TEMP)
            auto_control = user_input.get(CONF_AUTO_CONTROL, True)

            # Create zone via API
            session = async_get_clientsession(self.hass)
            client = SmartHeatingAPIClient(
                api_url=self._api_url,
                api_key=self._api_key,
                customer_id=self._customer_id,
                session=session,
            )
            client.installation_id = self._installation_id

            try:
                zone_result = await client.create_zone(
                    name=zone_name,
                    heating_type=heating_type,
                    temperature_entity_id=temp_entity,
                    climate_entity_id=climate_entity,
                    humidity_entity_id=humidity_entity,
                    power_entity_id=power_entity,
                    valve_entity_id=valve_entity,
                    supply_temp_entity_id=supply_temp_entity,
                    return_temp_entity_id=return_temp_entity,
                    min_temp=min_temp,
                    max_temp=max_temp,
                    target_temp=target_temp,
                    auto_control_enabled=auto_control,
                )

                self._zones.append(
                    {
                        "id": zone_result["id"],
                        "name": zone_name,
                        "heating_type": heating_type,
                        "temperature_entity": temp_entity,
                        "climate_entity": climate_entity,
                    }
                )

                # Ask if user wants to add another zone
                return await self.async_step_add_zone_prompt()

            except SmartHeatingAPIError as err:
                _LOGGER.error("Failed to create zone: %s", err)
                errors["base"] = "zone_creation_failed"

        return self.async_show_form(
            step_id="zone",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ZONE_NAME): str,
                    vol.Required(CONF_HEATING_TYPE, default=HEATING_TYPE_UNKNOWN): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=ht["value"], label=ht["label"])
                                for ht in HEATING_TYPES
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(CONF_TEMPERATURE_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=SENSOR_DOMAIN,
                            device_class="temperature",
                        )
                    ),
                    vol.Required(CONF_CLIMATE_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=CLIMATE_DOMAIN,
                        )
                    ),
                    vol.Optional(CONF_HUMIDITY_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=SENSOR_DOMAIN,
                            device_class="humidity",
                        )
                    ),
                    vol.Optional(CONF_POWER_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=SENSOR_DOMAIN,
                            device_class="power",
                        )
                    ),
                    vol.Optional(CONF_VALVE_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=[CLIMATE_DOMAIN, "number"],
                        )
                    ),
                    vol.Optional(CONF_SUPPLY_TEMP_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=SENSOR_DOMAIN,
                            device_class="temperature",
                        )
                    ),
                    vol.Optional(CONF_RETURN_TEMP_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=SENSOR_DOMAIN,
                            device_class="temperature",
                        )
                    ),
                    vol.Optional(CONF_MIN_TEMP, default=DEFAULT_MIN_TEMP): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=5,
                            max=30,
                            step=0.5,
                            unit_of_measurement=UnitOfTemperature.CELSIUS,
                        )
                    ),
                    vol.Optional(CONF_MAX_TEMP, default=DEFAULT_MAX_TEMP): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=10,
                            max=35,
                            step=0.5,
                            unit_of_measurement=UnitOfTemperature.CELSIUS,
                        )
                    ),
                    vol.Optional(CONF_TARGET_TEMP, default=DEFAULT_TARGET_TEMP): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=10,
                            max=30,
                            step=0.5,
                            unit_of_measurement=UnitOfTemperature.CELSIUS,
                        )
                    ),
                    vol.Optional(CONF_AUTO_CONTROL, default=True): bool,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> SmartHeatingOptionsFlow:
        """Get the options flow."""
        return SmartHeatingOptionsFlow(config_entry)


class SmartHeatingOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Smart Heating Optimizer."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._zones: list[dict[str, Any]] = []

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle options flow initialization."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_zone", "manage_zones", "settings"],
        )

    async def async_step_add_zone(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle adding a new zone."""
        errors: dict[str, str] = {}

        if user_input is not None:
            zone_name = user_input[CONF_ZONE_NAME]
            heating_type = user_input.get(CONF_HEATING_TYPE, HEATING_TYPE_UNKNOWN)
            temp_entity = user_input[CONF_TEMPERATURE_ENTITY]
            climate_entity = user_input[CONF_CLIMATE_ENTITY]
            humidity_entity = user_input.get(CONF_HUMIDITY_ENTITY)
            power_entity = user_input.get(CONF_POWER_ENTITY)
            valve_entity = user_input.get(CONF_VALVE_ENTITY)
            supply_temp_entity = user_input.get(CONF_SUPPLY_TEMP_ENTITY)
            return_temp_entity = user_input.get(CONF_RETURN_TEMP_ENTITY)
            min_temp = user_input.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)
            max_temp = user_input.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)
            target_temp = user_input.get(CONF_TARGET_TEMP, DEFAULT_TARGET_TEMP)
            auto_control = user_input.get(CONF_AUTO_CONTROL, True)

            # Create zone via API
            session = async_get_clientsession(self.hass)
            client = SmartHeatingAPIClient(
                api_url=self._config_entry.data[CONF_API_URL],
                api_key=self._config_entry.data[CONF_API_KEY],
                customer_id=self._config_entry.data[CONF_CUSTOMER_ID],
                session=session,
            )
            client.installation_id = self._config_entry.data[CONF_INSTALLATION_ID]

            try:
                await client.create_zone(
                    name=zone_name,
                    heating_type=heating_type,
                    temperature_entity_id=temp_entity,
                    climate_entity_id=climate_entity,
                    humidity_entity_id=humidity_entity,
                    power_entity_id=power_entity,
                    valve_entity_id=valve_entity,
                    supply_temp_entity_id=supply_temp_entity,
                    return_temp_entity_id=return_temp_entity,
                    min_temp=min_temp,
                    max_temp=max_temp,
                    target_temp=target_temp,
                    auto_control_enabled=auto_control,
                )

                # Trigger reload
                return self.async_create_entry(title="", data={})

            except SmartHeatingAPIError as err:
                _LOGGER.error("Failed to create zone: %s", err)
                errors["base"] = "zone_creation_failed"

        return self.async_show_form(
            step_id="add_zone",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ZONE_NAME): str,
                    vol.Required(CONF_HEATING_TYPE, default=HEATING_TYPE_UNKNOWN): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=ht["value"], label=ht["label"])
                                for ht in HEATING_TYPES
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(CONF_TEMPERATURE_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=SENSOR_DOMAIN,
                            device_class="temperature",
                        )
                    ),
                    vol.Required(CONF_CLIMATE_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=CLIMATE_DOMAIN,
                        )
                    ),
                    vol.Optional(CONF_HUMIDITY_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=SENSOR_DOMAIN,
                            device_class="humidity",
                        )
                    ),
                    vol.Optional(CONF_POWER_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=SENSOR_DOMAIN,
                            device_class="power",
                        )
                    ),
                    vol.Optional(CONF_VALVE_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=[CLIMATE_DOMAIN, "number"],
                        )
                    ),
                    vol.Optional(CONF_SUPPLY_TEMP_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=SENSOR_DOMAIN,
                            device_class="temperature",
                        )
                    ),
                    vol.Optional(CONF_RETURN_TEMP_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=SENSOR_DOMAIN,
                            device_class="temperature",
                        )
                    ),
                    vol.Optional(CONF_MIN_TEMP, default=DEFAULT_MIN_TEMP): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=5,
                            max=30,
                            step=0.5,
                            unit_of_measurement=UnitOfTemperature.CELSIUS,
                        )
                    ),
                    vol.Optional(CONF_MAX_TEMP, default=DEFAULT_MAX_TEMP): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=10,
                            max=35,
                            step=0.5,
                            unit_of_measurement=UnitOfTemperature.CELSIUS,
                        )
                    ),
                    vol.Optional(CONF_TARGET_TEMP, default=DEFAULT_TARGET_TEMP): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=10,
                            max=30,
                            step=0.5,
                            unit_of_measurement=UnitOfTemperature.CELSIUS,
                        )
                    ),
                    vol.Optional(CONF_AUTO_CONTROL, default=True): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_manage_zones(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle zone management."""
        # Fetch current zones
        session = async_get_clientsession(self.hass)
        client = SmartHeatingAPIClient(
            api_url=self._config_entry.data[CONF_API_URL],
            api_key=self._config_entry.data[CONF_API_KEY],
            customer_id=self._config_entry.data[CONF_CUSTOMER_ID],
            session=session,
        )
        client.installation_id = self._config_entry.data[CONF_INSTALLATION_ID]

        try:
            self._zones = await client.get_zones()
        except SmartHeatingAPIError:
            self._zones = []

        if not self._zones:
            return self.async_abort(reason="no_zones")

        zone_options = {zone["id"]: zone["name"] for zone in self._zones}

        if user_input is not None:
            selected_zone = user_input.get("zone")
            if selected_zone:
                # Store selected zone and go to edit
                self._selected_zone_id = selected_zone
                return await self.async_step_edit_zone()

        return self.async_show_form(
            step_id="manage_zones",
            data_schema=vol.Schema(
                {
                    vol.Required("zone"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=k, label=v)
                                for k, v in zone_options.items()
                            ],
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_edit_zone(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle zone editing."""
        # Find the selected zone
        zone = next(
            (z for z in self._zones if z["id"] == self._selected_zone_id),
            None,
        )

        if not zone:
            return self.async_abort(reason="zone_not_found")

        if user_input is not None:
            # Update the zone
            session = async_get_clientsession(self.hass)
            client = SmartHeatingAPIClient(
                api_url=self._config_entry.data[CONF_API_URL],
                api_key=self._config_entry.data[CONF_API_KEY],
                customer_id=self._config_entry.data[CONF_CUSTOMER_ID],
                session=session,
            )

            try:
                await client.update_zone(
                    zone_id=self._selected_zone_id,
                    name=user_input.get(CONF_ZONE_NAME),
                    heating_type=user_input.get(CONF_HEATING_TYPE),
                    valve_entity_id=user_input.get(CONF_VALVE_ENTITY),
                    supply_temp_entity_id=user_input.get(CONF_SUPPLY_TEMP_ENTITY),
                    return_temp_entity_id=user_input.get(CONF_RETURN_TEMP_ENTITY),
                    min_temp_c=user_input.get(CONF_MIN_TEMP),
                    max_temp_c=user_input.get(CONF_MAX_TEMP),
                    target_temp_c=user_input.get(CONF_TARGET_TEMP),
                    auto_control_enabled=user_input.get(CONF_AUTO_CONTROL),
                )
                return self.async_create_entry(title="", data={})
            except SmartHeatingAPIError as err:
                _LOGGER.error("Failed to update zone: %s", err)

        return self.async_show_form(
            step_id="edit_zone",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ZONE_NAME, default=zone.get("name", "")): str,
                    vol.Required(
                        CONF_HEATING_TYPE,
                        default=zone.get("heating_type", HEATING_TYPE_UNKNOWN),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=ht["value"], label=ht["label"])
                                for ht in HEATING_TYPES
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(
                        CONF_VALVE_ENTITY,
                        default=zone.get("valve_entity_id"),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=[CLIMATE_DOMAIN, "number"],
                        )
                    ),
                    vol.Optional(
                        CONF_SUPPLY_TEMP_ENTITY,
                        default=zone.get("supply_temp_entity_id"),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=SENSOR_DOMAIN,
                            device_class="temperature",
                        )
                    ),
                    vol.Optional(
                        CONF_RETURN_TEMP_ENTITY,
                        default=zone.get("return_temp_entity_id"),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=SENSOR_DOMAIN,
                            device_class="temperature",
                        )
                    ),
                    vol.Optional(
                        CONF_MIN_TEMP,
                        default=zone.get("min_temp_c", DEFAULT_MIN_TEMP),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=5,
                            max=30,
                            step=0.5,
                            unit_of_measurement=UnitOfTemperature.CELSIUS,
                        )
                    ),
                    vol.Optional(
                        CONF_MAX_TEMP,
                        default=zone.get("max_temp_c", DEFAULT_MAX_TEMP),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=10,
                            max=35,
                            step=0.5,
                            unit_of_measurement=UnitOfTemperature.CELSIUS,
                        )
                    ),
                    vol.Optional(
                        CONF_TARGET_TEMP,
                        default=zone.get("target_temp_c", DEFAULT_TARGET_TEMP),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=10,
                            max=30,
                            step=0.5,
                            unit_of_measurement=UnitOfTemperature.CELSIUS,
                        )
                    ),
                    vol.Optional(
                        CONF_AUTO_CONTROL,
                        default=zone.get("auto_control_enabled", True),
                    ): bool,
                }
            ),
            description_placeholders={
                "zone_name": zone.get("name", "Zone"),
            },
        )

    async def async_step_settings(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle settings update."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Update installation settings
            session = async_get_clientsession(self.hass)
            client = SmartHeatingAPIClient(
                api_url=self._config_entry.data[CONF_API_URL],
                api_key=self._config_entry.data[CONF_API_KEY],
                customer_id=self._config_entry.data[CONF_CUSTOMER_ID],
                session=session,
            )
            client.installation_id = self._config_entry.data[CONF_INSTALLATION_ID]

            try:
                await client.update_installation(
                    price_area=user_input.get(CONF_PRICE_AREA),
                    outdoor_temp_entity_id=user_input.get(CONF_OUTDOOR_TEMP_ENTITY),
                )
                return self.async_create_entry(title="", data={})
            except SmartHeatingAPIError as err:
                _LOGGER.error("Failed to update settings: %s", err)
                errors["base"] = "update_failed"

        current_price_area = self._config_entry.data.get(CONF_PRICE_AREA, DEFAULT_PRICE_AREA)
        current_outdoor_entity = self._config_entry.data.get(CONF_OUTDOOR_TEMP_ENTITY)

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PRICE_AREA, default=current_price_area): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=PRICE_AREAS,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(
                        CONF_OUTDOOR_TEMP_ENTITY,
                        default=current_outdoor_entity,
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=SENSOR_DOMAIN,
                            device_class="temperature",
                        )
                    ),
                }
            ),
            errors=errors,
        )
