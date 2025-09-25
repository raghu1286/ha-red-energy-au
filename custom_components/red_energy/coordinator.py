"""Data coordinator for the Red Energy integration."""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import RedEnergyAuthError, RedEnergyClient, RedEnergyError
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN
from .models import (
    DailyUsageEntry,
    EnergyBreakdown,
    EnergyPeriod,
    HourlyUsage,
    PropertyEnergyData,
    SelectedProperty,
)

_LOGGER = logging.getLogger(__name__)


class RedEnergyCoordinator(DataUpdateCoordinator[dict[str, PropertyEnergyData]]):
    """Coordinates fetching data from the Red Energy API."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        client: RedEnergyClient,
        selected_properties: Iterable[SelectedProperty],
        update_interval: timedelta | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} coordinator",
            update_interval=update_interval or DEFAULT_UPDATE_INTERVAL,
        )
        self._client = client
        self._selected_properties: list[SelectedProperty] = [
            SelectedProperty(
                property_id=prop.property_id,
                consumer_number=prop.consumer_number,
                name=prop.name,
            )
            for prop in selected_properties
            if prop.property_id
        ]

    async def _async_update_data(self) -> dict[str, PropertyEnergyData]:
        try:
            properties = await self._client.async_get_properties()
        except RedEnergyAuthError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except RedEnergyError as err:
            raise UpdateFailed(f"Failed to retrieve properties: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Unexpected error fetching properties: {err}") from err

        properties_by_id = {
            pid: raw
            for raw in properties
            if isinstance(raw, dict) and (pid := _extract_property_id(raw))
        }
        _LOGGER.debug(
            "Coordinator discovered %d properties from API payload", len(properties_by_id)
        )

        results: dict[str, PropertyEnergyData] = {}
        now = dt_util.utcnow()

        for selected in self._selected_properties:
            raw = properties_by_id.get(selected.property_id)
            name = selected.name
            consumer_number = selected.consumer_number

            if raw:
                name = _extract_property_name(raw) or name
                dynamic_consumer = _extract_electricity_consumer(raw)
                if dynamic_consumer:
                    consumer_number = dynamic_consumer
            else:
                _LOGGER.debug(
                    "Configured property %s not returned by API; using stored consumer number",
                    selected.property_id,
                )

            if not consumer_number:
                _LOGGER.warning(
                    "Skipping property %s because no consumer number is available",
                    selected.property_id,
                )
                continue

            _LOGGER.debug(
                "Fetching usage for property %s (consumer %s)",
                selected.property_id,
                consumer_number,
            )
            try:
                daily_entries = await self._client.async_get_daily_usage_entries(consumer_number)
                interval_payload = await self._client.async_get_interval_usage(consumer_number)
            except RedEnergyAuthError as err:
                raise UpdateFailed(
                    f"Authentication failed while fetching usage for {selected.property_id}: {err}"
                ) from err
            except RedEnergyError as err:
                raise UpdateFailed(
                    f"Failed to retrieve usage for {selected.property_id}: {err}"
                ) from err
            except Exception as err:  # noqa: BLE001
                raise UpdateFailed(
                    f"Unexpected error fetching usage for {selected.property_id}: {err}"
                ) from err

            breakdown = _build_breakdown(daily_entries)
            latest_hour = _build_latest_hour(interval_payload)
            results[selected.property_id] = PropertyEnergyData(
                property_id=selected.property_id,
                name=name,
                breakdown=breakdown,
                last_updated=now,
                consumer_number=consumer_number,
                latest_hour=latest_hour,
            )
            selected.name = name
            selected.consumer_number = consumer_number

            _LOGGER.debug(
                "Property %s fetched %d usage entries",
                selected.property_id,
                len(daily_entries),
            )

        return results


def _extract_property_id(raw: dict[str, Any]) -> str | None:
    for key in (
        "id",
        "propertyNumber",
        "propertyId",
        "accountNumber",
        "accountId",
    ):
        value = raw.get(key)
        if value:
            return str(value)
    return None


def _extract_property_name(raw: dict[str, Any]) -> str | None:
    for key in ("name", "displayName"):
        value = raw.get(key)
        if value:
            return str(value)
    address = raw.get("address") or {}
    display_address = address.get("displayAddress") or address.get("gentrackDisplayAddress")
    if display_address:
        return str(display_address).replace("\n", ", ").strip()
    return None


def _extract_electricity_consumer(raw: dict[str, Any]) -> str | None:
    services = raw.get("services")
    if not isinstance(services, list):
        return None

    for service in services:
        if not isinstance(service, dict):
            continue
        service_type = str(service.get("type") or service.get("serviceType") or "").lower()
        if service_type not in {"electricity", "elec"}:
            continue
        for key in ("consumerNumber", "consumer_number", "consumerNo"):
            value = service.get(key)
            if value:
                return str(value)
    return None


def _build_breakdown(entries: list[DailyUsageEntry]) -> EnergyBreakdown:
    if entries:
        latest_day = entries[-1].day
    else:
        latest_day = dt_util.utcnow().date()

    daily_entries = [entry for entry in entries if entry.day == latest_day]
    weekly_start = latest_day - timedelta(days=6)
    weekly_entries = [entry for entry in entries if entry.day >= weekly_start]
    month_start = latest_day.replace(day=1)
    monthly_entries = [entry for entry in entries if entry.day >= month_start]

    return EnergyBreakdown(
        daily=_build_period(daily_entries, latest_day, latest_day),
        weekly=_build_period(weekly_entries, weekly_start, latest_day),
        monthly=_build_period(monthly_entries, month_start, latest_day),
    )


def _build_period(
    entries: Iterable[DailyUsageEntry],
    default_start: date,
    end: date,
) -> EnergyPeriod:
    entry_list = list(entries)
    if entry_list:
        start = entry_list[0].day
        consumption = sum(entry.consumption_kwh for entry in entry_list)
        generation = sum(entry.generation_kwh for entry in entry_list)
        consumption_cost = sum(entry.consumption_cost for entry in entry_list)
        generation_value = sum(entry.generation_value for entry in entry_list)
    else:
        start = default_start
        consumption = 0.0
        generation = 0.0
        consumption_cost = 0.0
        generation_value = 0.0

    return EnergyPeriod(
        start=start,
        end=end,
        consumption_kwh=round(consumption, 3),
        generation_kwh=round(generation, 3),
        consumption_cost=round(consumption_cost, 2),
        generation_value=round(generation_value, 2),
    )


def _build_latest_hour(interval_payload: list[dict[str, Any]]) -> HourlyUsage | None:
    """Aggregate interval payload into the latest hourly summary."""
    buckets: dict[datetime, dict[str, float]] = {}

    for day_block in interval_payload:
        half_hours = day_block.get("halfHours")
        if not isinstance(half_hours, list):
            continue
        for interval in half_hours:
            if not isinstance(interval, dict):
                continue
            start_str = interval.get("intervalStart")
            start_dt = dt_util.parse_datetime(start_str) if start_str else None
            if not start_dt:
                continue
            hour_start = start_dt.replace(minute=0, second=0, microsecond=0)
            bucket = buckets.setdefault(
                hour_start,
                {
                    "consumption_kwh": 0.0,
                    "generation_kwh": 0.0,
                    "consumption_cost": 0.0,
                    "generation_value": 0.0,
                },
            )
            bucket["consumption_kwh"] += float(interval.get("consumptionKwh") or 0)
            bucket["generation_kwh"] += float(interval.get("generationKwh") or 0)
            bucket["consumption_cost"] += float(
                interval.get("consumptionDollarIncGst")
                or interval.get("consumptionDollar")
                or 0
            )
            bucket["generation_value"] += float(
                interval.get("generationDollar")
                or interval.get("generationDollarIncGst")
                or 0
            )

    if not buckets:
        return None

    latest_start = max(buckets)
    data = buckets[latest_start]
    return HourlyUsage(
        start=latest_start,
        end=latest_start + timedelta(hours=1),
        consumption_kwh=round(data["consumption_kwh"], 3),
        generation_kwh=round(data["generation_kwh"], 3),
        consumption_cost=round(data["consumption_cost"], 2),
        generation_value=round(data["generation_value"], 2),
    )
