"""Data coordinator for the Red Energy integration."""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import RedEnergyAuthError, RedEnergyClient, RedEnergyError
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN
from .models import DailyUsageEntry, EnergyBreakdown, EnergyPeriod, PropertyEnergyData

_LOGGER = logging.getLogger(__name__)


class RedEnergyCoordinator(DataUpdateCoordinator[dict[str, PropertyEnergyData]]):
    """Coordinates fetching data from the Red Energy API."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        client: RedEnergyClient,
        property_ids: Iterable[str] | None,
        update_interval: timedelta | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} coordinator",
            update_interval=update_interval or DEFAULT_UPDATE_INTERVAL,
        )
        self._client = client
        self._configured_property_ids = {pid for pid in property_ids or [] if pid}

    async def _async_update_data(self) -> dict[str, PropertyEnergyData]:
        try:
            properties = await self._client.async_get_properties()
        except RedEnergyAuthError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except RedEnergyError as err:
            raise UpdateFailed(f"Failed to retrieve properties: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Unexpected error fetching properties: {err}") from err

        property_map = _select_properties(properties, self._configured_property_ids)
        if not property_map:
            raise UpdateFailed("No electricity services available for the configured properties")

        results: dict[str, PropertyEnergyData] = {}
        now = dt_util.utcnow()

        for prop_id, metadata in property_map.items():
            consumer_number = metadata["consumer_number"]
            try:
                daily_entries = await self._client.async_get_daily_usage_entries(consumer_number)
            except RedEnergyAuthError as err:
                raise UpdateFailed(f"Authentication failed while fetching usage for {prop_id}: {err}") from err
            except RedEnergyError as err:
                raise UpdateFailed(f"Failed to retrieve usage for {prop_id}: {err}") from err
            except Exception as err:  # noqa: BLE001
                raise UpdateFailed(f"Unexpected error fetching usage for {prop_id}: {err}") from err

            breakdown = _build_breakdown(daily_entries)
            results[prop_id] = PropertyEnergyData(
                property_id=prop_id,
                name=metadata["name"],
                breakdown=breakdown,
                last_updated=now,
                consumer_number=consumer_number,
            )

        return results


def _select_properties(
    properties: list[dict[str, Any]], configured_ids: set[str],
) -> dict[str, dict[str, str]]:
    """Return a mapping of property id -> metadata for electricity services."""
    property_map: dict[str, dict[str, str]] = {}

    for raw in properties:
        prop_id = _extract_property_id(raw)
        if not prop_id:
            continue
        if configured_ids and prop_id not in configured_ids:
            continue

        name = _extract_property_name(raw) or f"Property {prop_id}"
        consumer_number = _extract_electricity_consumer(raw)
        if not consumer_number:
            continue

        property_map[prop_id] = {"name": name, "consumer_number": consumer_number}

    if not property_map and not configured_ids:
        _LOGGER.debug("No electricity services found in property payload: %s", properties)
    elif configured_ids:
        missing = configured_ids - property_map.keys()
        if missing:
            _LOGGER.warning("Configured properties missing electricity data: %s", sorted(missing))

    return property_map


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
    else:
        start = default_start
        consumption = 0.0
        generation = 0.0

    return EnergyPeriod(
        start=start,
        end=end,
        consumption_kwh=round(consumption, 3),
        generation_kwh=round(generation, 3),
    )
