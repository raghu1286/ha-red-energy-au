"""Sensor platform for Red Energy energy tracking."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_DOLLAR, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DATA_COORDINATOR,
    DOMAIN,
    SENSOR_KIND_DAILY_ELECTRICITY,
    SENSOR_KIND_DAILY_ELECTRICITY_COST,
    SENSOR_KIND_DAILY_SOLAR,
    SENSOR_KIND_DAILY_SOLAR_VALUE,
    SENSOR_KIND_MONTHLY_ELECTRICITY,
    SENSOR_KIND_MONTHLY_ELECTRICITY_COST,
    SENSOR_KIND_MONTHLY_SOLAR,
    SENSOR_KIND_MONTHLY_SOLAR_VALUE,
    SENSOR_KIND_HOURLY_ELECTRICITY,
    SENSOR_KIND_HOURLY_ELECTRICITY_COST,
    SENSOR_KIND_HOURLY_SOLAR,
    SENSOR_KIND_HOURLY_SOLAR_VALUE,
    SENSOR_KIND_WEEKLY_ELECTRICITY,
    SENSOR_KIND_WEEKLY_ELECTRICITY_COST,
    SENSOR_KIND_WEEKLY_SOLAR,
    SENSOR_KIND_WEEKLY_SOLAR_VALUE,
)
from .coordinator import RedEnergyCoordinator
from .models import EnergyBreakdown, EnergyPeriod, PropertyEnergyData

FRIENDLY_SENSOR_NAMES = {
    "daily_electricity": "Red Energy Daily Electricity",
    "daily_electricity_cost": "Red Energy Daily Electricity Cost",
    "weekly_electricity": "Red Energy Weekly Electricity",
    "weekly_electricity_cost": "Red Energy Weekly Electricity Cost",
    "monthly_electricity": "Red Energy Monthly Electricity",
    "monthly_electricity_cost": "Red Energy Monthly Electricity Cost",
    "daily_solar": "Red Energy Daily Solar",
    "daily_solar_value": "Red Energy Daily Solar Value",
    "weekly_solar": "Red Energy Weekly Solar",
    "weekly_solar_value": "Red Energy Weekly Solar Value",
    "monthly_solar": "Red Energy Monthly Solar",
    "monthly_solar_value": "Red Energy Monthly Solar Value",
    "hourly_electricity": "Red Energy Hourly Electricity",
    "hourly_electricity_cost": "Red Energy Hourly Electricity Cost",
    "hourly_solar": "Red Energy Hourly Solar",
    "hourly_solar_value": "Red Energy Hourly Solar Value",
}


@dataclass(frozen=True, slots=True)
class RedEnergySensorDescription(SensorEntityDescription):
    """Describe a Red Energy sensor."""

    period: str = ""
    metric: str = ""


SENSOR_DESCRIPTIONS: tuple[RedEnergySensorDescription, ...] = (
    RedEnergySensorDescription(
        key=SENSOR_KIND_DAILY_ELECTRICITY,
        translation_key="daily_electricity",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period="daily",
        metric="consumption",
    ),
    RedEnergySensorDescription(
        key=SENSOR_KIND_DAILY_ELECTRICITY_COST,
        translation_key="daily_electricity_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_DOLLAR,
        period="daily",
        metric="consumption_cost",
    ),
    RedEnergySensorDescription(
        key=SENSOR_KIND_WEEKLY_ELECTRICITY,
        translation_key="weekly_electricity",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period="weekly",
        metric="consumption",
    ),
    RedEnergySensorDescription(
        key=SENSOR_KIND_WEEKLY_ELECTRICITY_COST,
        translation_key="weekly_electricity_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_DOLLAR,
        period="weekly",
        metric="consumption_cost",
    ),
    RedEnergySensorDescription(
        key=SENSOR_KIND_MONTHLY_ELECTRICITY,
        translation_key="monthly_electricity",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period="monthly",
        metric="consumption",
    ),
    RedEnergySensorDescription(
        key=SENSOR_KIND_MONTHLY_ELECTRICITY_COST,
        translation_key="monthly_electricity_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_DOLLAR,
        period="monthly",
        metric="consumption_cost",
    ),
    RedEnergySensorDescription(
        key=SENSOR_KIND_DAILY_SOLAR,
        translation_key="daily_solar",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period="daily",
        metric="generation",
    ),
    RedEnergySensorDescription(
        key=SENSOR_KIND_DAILY_SOLAR_VALUE,
        translation_key="daily_solar_value",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_DOLLAR,
        period="daily",
        metric="generation_cost",
    ),
    RedEnergySensorDescription(
        key=SENSOR_KIND_WEEKLY_SOLAR,
        translation_key="weekly_solar",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period="weekly",
        metric="generation",
    ),
    RedEnergySensorDescription(
        key=SENSOR_KIND_WEEKLY_SOLAR_VALUE,
        translation_key="weekly_solar_value",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_DOLLAR,
        period="weekly",
        metric="generation_cost",
    ),
    RedEnergySensorDescription(
        key=SENSOR_KIND_MONTHLY_SOLAR,
        translation_key="monthly_solar",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period="monthly",
        metric="generation",
    ),
    RedEnergySensorDescription(
        key=SENSOR_KIND_MONTHLY_SOLAR_VALUE,
        translation_key="monthly_solar_value",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_DOLLAR,
        period="monthly",
        metric="generation_cost",
    ),
    RedEnergySensorDescription(
        key=SENSOR_KIND_HOURLY_ELECTRICITY,
        translation_key="hourly_electricity",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period="hourly",
        metric="consumption",
    ),
    RedEnergySensorDescription(
        key=SENSOR_KIND_HOURLY_ELECTRICITY_COST,
        translation_key="hourly_electricity_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CURRENCY_DOLLAR,
        period="hourly",
        metric="consumption_cost",
    ),
    RedEnergySensorDescription(
        key=SENSOR_KIND_HOURLY_SOLAR,
        translation_key="hourly_solar",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period="hourly",
        metric="generation",
    ),
    RedEnergySensorDescription(
        key=SENSOR_KIND_HOURLY_SOLAR_VALUE,
        translation_key="hourly_solar_value",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CURRENCY_DOLLAR,
        period="hourly",
        metric="generation_cost",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Red Energy sensors for a config entry."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator: RedEnergyCoordinator = runtime[DATA_COORDINATOR]

    sensors: list[RedEnergySensor] = []
    for property_id in sorted(coordinator.data or {}):
        for description in SENSOR_DESCRIPTIONS:
            sensors.append(
                RedEnergySensor(
                    coordinator=coordinator,
                    entry=entry,
                    property_id=property_id,
                    description=description,
                )
            )

    async_add_entities(sensors)


class RedEnergySensor(CoordinatorEntity[RedEnergyCoordinator], SensorEntity):
    """Base sensor representing Red Energy energy usage."""

    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: RedEnergyCoordinator,
        entry: ConfigEntry,
        *,
        property_id: str,
        description: RedEnergySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._property_id = property_id
        self._attr_unique_id = f"{entry.entry_id}_{property_id}_{description.key}"
        self._attr_translation_key = description.translation_key
        self._attr_name = FRIENDLY_SENSOR_NAMES.get(
            description.translation_key,
            f"Red Energy {description.key.replace('_', ' ').title()}"
        )

    @property
    def _property_data(self) -> PropertyEnergyData | None:
        return self.coordinator.data.get(self._property_id) if self.coordinator.data else None

    @property
    def available(self) -> bool:
        return self._property_data is not None and super().available

    @property
    def native_value(self) -> float | None:
        property_data = self._property_data
        if not property_data:
            return None

        metric = self.entity_description.metric

        if self.entity_description.period == "hourly":
            latest = property_data.latest_hour
            if not latest:
                return None
            if metric == "consumption":
                return latest.consumption_kwh
            if metric == "generation":
                return latest.generation_kwh
            if metric == "consumption_cost":
                return latest.consumption_cost
            if metric == "generation_cost":
                return latest.generation_value
            return None

        period = _select_period(property_data.breakdown, self.entity_description.period)
        if period is None:
            return None

        if metric == "generation":
            return period.generation_kwh
        if metric == "consumption":
            return period.consumption_kwh
        if metric == "consumption_cost":
            return period.consumption_cost
        if metric == "generation_cost":
            return period.generation_value
        return None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        property_data = self._property_data
        if not property_data:
            return None

        if self.entity_description.period == "hourly":
            latest = property_data.latest_hour
            if not latest:
                return None
            return {
                "property_id": property_data.property_id,
                "period_start": latest.start.isoformat(),
                "period_end": latest.end.isoformat(),
                "metric": self.entity_description.metric,
                "last_updated": property_data.last_updated.isoformat(),
                "consumption_kwh": latest.consumption_kwh,
                "generation_kwh": latest.generation_kwh,
                "consumption_cost": latest.consumption_cost,
                "generation_value": latest.generation_value,
            }

        period = _select_period(property_data.breakdown, self.entity_description.period)
        if period is None:
            return None

        last_updated = property_data.last_updated.date().isoformat()

        return {
            "property_id": property_data.property_id,
            "period_start": period.start.isoformat(),
            "period_end": period.end.isoformat(),
            "metric": self.entity_description.metric,
            "last_updated": last_updated,
            "consumption_kwh": period.consumption_kwh,
            "generation_kwh": period.generation_kwh,
            "consumption_cost": period.consumption_cost,
            "generation_value": period.generation_value,
        }

    @property
    def device_info(self) -> dict[str, object] | None:
        property_data = self._property_data
        if not property_data:
            return None
        return {
            "identifiers": {(DOMAIN, property_data.property_id)},
            "name": property_data.name,
            "manufacturer": "Red Energy",
        }


def _select_period(breakdown: EnergyBreakdown, period_key: str) -> EnergyPeriod | None:
    if period_key == "daily":
        return breakdown.daily
    if period_key == "weekly":
        return breakdown.weekly
    if period_key == "monthly":
        return breakdown.monthly
    return None
