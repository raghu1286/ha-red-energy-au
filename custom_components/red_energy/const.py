"""Constants for the Red Energy integration."""
from __future__ import annotations

from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "red_energy"
PLATFORMS: Final = [Platform.SENSOR]

CONF_PROPERTY_IDS: Final = "property_ids"
CONF_SELECTED_PROPERTIES: Final = "selected_properties"
CONF_UPDATE_INTERVAL: Final = "update_interval"

DATA_CLIENT: Final = "client"
DATA_COORDINATOR: Final = "coordinator"

DEFAULT_UPDATE_INTERVAL: Final = timedelta(minutes=30)
MINIMUM_UPDATE_INTERVAL: Final = timedelta(minutes=15)
MAXIMUM_UPDATE_INTERVAL: Final = timedelta(hours=2)

STEP_SELECT_PROPERTIES: Final = "select_properties"

PROPERTY_KEY_ID: Final = "property_id"
PROPERTY_KEY_NAME: Final = "name"
PROPERTY_KEY_CONSUMER: Final = "consumer_number"

SENSOR_KIND_DAILY_ELECTRICITY: Final = "daily_electricity"
SENSOR_KIND_WEEKLY_ELECTRICITY: Final = "weekly_electricity"
SENSOR_KIND_MONTHLY_ELECTRICITY: Final = "monthly_electricity"
SENSOR_KIND_DAILY_SOLAR: Final = "daily_solar"
SENSOR_KIND_WEEKLY_SOLAR: Final = "weekly_solar"
SENSOR_KIND_MONTHLY_SOLAR: Final = "monthly_solar"
SENSOR_KIND_DAILY_ELECTRICITY_COST: Final = "daily_electricity_cost"
SENSOR_KIND_WEEKLY_ELECTRICITY_COST: Final = "weekly_electricity_cost"
SENSOR_KIND_MONTHLY_ELECTRICITY_COST: Final = "monthly_electricity_cost"
SENSOR_KIND_DAILY_SOLAR_VALUE: Final = "daily_solar_value"
SENSOR_KIND_WEEKLY_SOLAR_VALUE: Final = "weekly_solar_value"
SENSOR_KIND_MONTHLY_SOLAR_VALUE: Final = "monthly_solar_value"
SENSOR_KIND_HOURLY_ELECTRICITY: Final = "hourly_electricity"
SENSOR_KIND_HOURLY_ELECTRICITY_COST: Final = "hourly_electricity_cost"
SENSOR_KIND_HOURLY_SOLAR: Final = "hourly_solar"
SENSOR_KIND_HOURLY_SOLAR_VALUE: Final = "hourly_solar_value"
