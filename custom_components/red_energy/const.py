"""Constants for the Red Energy integration."""
from __future__ import annotations

from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "red_energy"
PLATFORMS: Final = [Platform.SENSOR]

CONF_PROPERTY_IDS: Final = "property_ids"
CONF_UPDATE_INTERVAL: Final = "update_interval"

DATA_CLIENT: Final = "client"
DATA_COORDINATOR: Final = "coordinator"

DEFAULT_UPDATE_INTERVAL: Final = timedelta(minutes=30)
MINIMUM_UPDATE_INTERVAL: Final = timedelta(minutes=15)
MAXIMUM_UPDATE_INTERVAL: Final = timedelta(hours=2)

STEP_SELECT_PROPERTIES: Final = "select_properties"

SENSOR_KIND_DAILY_ELECTRICITY: Final = "daily_electricity"
SENSOR_KIND_WEEKLY_ELECTRICITY: Final = "weekly_electricity"
SENSOR_KIND_MONTHLY_ELECTRICITY: Final = "monthly_electricity"
SENSOR_KIND_DAILY_SOLAR: Final = "daily_solar"
SENSOR_KIND_WEEKLY_SOLAR: Final = "weekly_solar"
SENSOR_KIND_MONTHLY_SOLAR: Final = "monthly_solar"
