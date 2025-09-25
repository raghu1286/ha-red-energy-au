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
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DATA_COORDINATOR,
    DOMAIN,
    SENSOR_KIND_DAILY_ELECTRICITY,
    SENSOR_KIND_DAILY_SOLAR,
    SENSOR_KIND_MONTHLY_ELECTRICITY,
    SENSOR_KIND_MONTHLY_SOLAR,
    SENSOR_KIND_WEEKLY_ELECTRICITY,
    SENSOR_KIND_WEEKLY_SOLAR,
)
from .coordinator import RedEnergyCoordinator
from .models import EnergyBreakdown, EnergyPeriod, PropertyEnergyData


@dataclass(frozen=True, slots=True)
class RedEnergySensorDescription(SensorEntityDescription):
    """Describe a Red Energy sensor."""

    period: str
    metric: str


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
        key=SENSOR_KIND_WEEKLY_ELECTRICITY,
        translation_key="weekly_electricity",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period="weekly",
        metric="consumption",
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
        key=SENSOR_KIND_DAILY_SOLAR,
        translation_key="daily_solar",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period="daily",
        metric="generation",
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
        key=SENSOR_KIND_MONTHLY_SOLAR,
        translation_key="monthly_solar",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period="monthly",
        metric="generation",
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

    _attr_has_entity_name = True

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

        period = _select_period(property_data.breakdown, self.entity_description.period)
        if period is None:
            return None

        if self.entity_description.metric == "generation":
            return period.generation_kwh
        return period.consumption_kwh

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        property_data = self._property_data
        if not property_data:
            return None

        period = _select_period(property_data.breakdown, self.entity_description.period)
        if period is None:
            return None

        return {
            "property_id": property_data.property_id,
            "period_start": period.start.isoformat(),
            "period_end": period.end.isoformat(),
            "metric": self.entity_description.metric,
            "last_updated": property_data.last_updated.isoformat(),
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
