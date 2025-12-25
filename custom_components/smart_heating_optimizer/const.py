"""Constants for Smart Heating Optimizer integration."""

from typing import Final

DOMAIN: Final = "smart_heating_optimizer"

# Configuration keys
CONF_API_URL: Final = "api_url"
CONF_API_KEY: Final = "api_key"
CONF_CUSTOMER_ID: Final = "customer_id"
CONF_INSTALLATION_ID: Final = "installation_id"
CONF_OUTDOOR_TEMP_ENTITY: Final = "outdoor_temp_entity"
CONF_PRICE_AREA: Final = "price_area"

# Zone configuration
CONF_ZONE_NAME: Final = "zone_name"
CONF_TEMPERATURE_ENTITY: Final = "temperature_entity"
CONF_HUMIDITY_ENTITY: Final = "humidity_entity"
CONF_CLIMATE_ENTITY: Final = "climate_entity"
CONF_POWER_ENTITY: Final = "power_entity"
CONF_MIN_TEMP: Final = "min_temp"
CONF_MAX_TEMP: Final = "max_temp"
CONF_TARGET_TEMP: Final = "target_temp"
CONF_AUTO_CONTROL: Final = "auto_control"

# Default values
DEFAULT_API_URL: Final = "https://iot.jtec.io/api/v1"
DEFAULT_PRICE_AREA: Final = "SE3"
DEFAULT_MIN_TEMP: Final = 16.0
DEFAULT_MAX_TEMP: Final = 24.0
DEFAULT_TARGET_TEMP: Final = 20.0
DEFAULT_TELEMETRY_INTERVAL: Final = 300  # 5 minutes

# Supported price areas (Nordic)
PRICE_AREAS: Final = [
    "SE1", "SE2", "SE3", "SE4",  # Sweden
    "NO1", "NO2", "NO3", "NO4", "NO5",  # Norway
    "DK1", "DK2",  # Denmark
    "FI",  # Finland
]

# Platforms
PLATFORMS: Final = ["sensor", "switch"]

# Update intervals
SCAN_INTERVAL: Final = 60  # seconds for coordinator
TELEMETRY_INTERVAL: Final = 300  # seconds between telemetry sends

# MQTT topics (relative to installation)
MQTT_TOPIC_SETPOINT: Final = "ha/{installation_id}/zone/{zone_id}/setpoint"
MQTT_TOPIC_STATUS: Final = "ha/{installation_id}/zone/{zone_id}/status"

# API endpoints
API_REGISTER: Final = "/ha-integration/register"
API_INSTALLATION: Final = "/ha-integration/installation"
API_ZONES: Final = "/ha-integration/zones"
API_TELEMETRY: Final = "/ha-integration/telemetry"
API_OPTIMIZE: Final = "/ha-integration/optimize"
API_DASHBOARD: Final = "/ha-integration/dashboard"

# Zone statuses
STATUS_INITIALIZING: Final = "initializing"
STATUS_COLLECTING: Final = "collecting"
STATUS_LEARNING: Final = "learning"
STATUS_OPTIMIZING: Final = "optimizing"
STATUS_MANUAL: Final = "manual"
STATUS_ERROR: Final = "error"

# Optimization modes
MODE_ECONOMY: Final = "economy"
MODE_COMFORT: Final = "comfort"
MODE_BALANCED: Final = "balanced"

# Sensor entity descriptions
SENSOR_STATUS: Final = "status"
SENSOR_SAVINGS_TODAY: Final = "savings_today"
SENSOR_SAVINGS_TOTAL: Final = "savings_total"
SENSOR_NEXT_CHANGE: Final = "next_change"
SENSOR_ML_ACCURACY: Final = "ml_accuracy"
SENSOR_OBSERVATIONS: Final = "observations"

# Switch entity descriptions
SWITCH_AUTO_CONTROL: Final = "auto_control"

# Icons
ICON_HEATING: Final = "mdi:radiator"
ICON_SAVINGS: Final = "mdi:cash-multiple"
ICON_STATUS: Final = "mdi:information-outline"
ICON_ML: Final = "mdi:brain"
ICON_AUTO: Final = "mdi:robot"

# Attributes
ATTR_ZONE_ID: Final = "zone_id"
ATTR_INSTALLATION_ID: Final = "installation_id"
ATTR_ML_STATUS: Final = "ml_status"
ATTR_OBSERVATIONS_NEEDED: Final = "observations_needed"
ATTR_NEXT_SETPOINT: Final = "next_setpoint"
ATTR_NEXT_SETPOINT_TIME: Final = "next_setpoint_time"
ATTR_NEXT_SETPOINT_REASON: Final = "next_setpoint_reason"
ATTR_CURRENT_PRICE: Final = "current_price"
ATTR_IS_CHEAP_PERIOD: Final = "is_cheap_period"
