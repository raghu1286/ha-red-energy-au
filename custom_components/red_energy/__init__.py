"""The Red Energy integration."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CLIENT_ID, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import RedEnergyClient
from .const import (
    CONF_PROPERTY_IDS,
    CONF_SELECTED_PROPERTIES,
    CONF_UPDATE_INTERVAL,
    DATA_CLIENT,
    DATA_COORDINATOR,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAXIMUM_UPDATE_INTERVAL,
    MINIMUM_UPDATE_INTERVAL,
    PLATFORMS,
    PROPERTY_KEY_CONSUMER,
    PROPERTY_KEY_ID,
    PROPERTY_KEY_NAME,
)
from .coordinator import RedEnergyCoordinator
from .models import SelectedProperty


_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up via YAML is not supported."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry."""
    hass.data.setdefault(DOMAIN, {})

    session = async_get_clientsession(hass)
    client = RedEnergyClient(
        session,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        client_id=entry.data[CONF_CLIENT_ID],
    )

    selected_properties = await _async_resolve_selected_properties(hass, entry, client)
    if not selected_properties:
        raise ConfigEntryNotReady("No valid Red Energy properties configured")

    _LOGGER.debug(
        "Setting up Red Energy entry %s with properties=%s",
        entry.entry_id,
        [prop.property_id for prop in selected_properties],
    )

    update_interval = _resolve_update_interval(entry.options)

    coordinator = RedEnergyCoordinator(
        hass,
        client=client,
        selected_properties=selected_properties,
        update_interval=update_interval,
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_CLIENT: client,
        DATA_COORDINATOR: coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Currently no config migrations are required."""
    return True


def _resolve_update_interval(options: Mapping[str, object]) -> timedelta:
    minutes = options.get(CONF_UPDATE_INTERVAL) if options else None
    if not minutes:
        return DEFAULT_UPDATE_INTERVAL

    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return DEFAULT_UPDATE_INTERVAL

    clamped = max(MINIMUM_UPDATE_INTERVAL.total_seconds() / 60, min(minutes, MAXIMUM_UPDATE_INTERVAL.total_seconds() / 60))
    return timedelta(minutes=clamped)


async def _async_resolve_selected_properties(
    hass: HomeAssistant,
    entry: ConfigEntry,
    client: RedEnergyClient,
) -> list[SelectedProperty]:
    stored = entry.data.get(CONF_SELECTED_PROPERTIES)
    if stored:
        _LOGGER.debug("Using %d stored Red Energy properties", len(stored))
        return [_selected_property_from_dict(item) for item in stored]

    legacy_ids: Iterable[str] = entry.data.get(CONF_PROPERTY_IDS, [])
    if not legacy_ids:
        _LOGGER.warning("No properties configured for Red Energy entry %s", entry.entry_id)
        return []

    _LOGGER.debug("Upgrading legacy property selection for entry %s", entry.entry_id)
    properties_payload = await client.async_get_properties()
    by_id: dict[str, Mapping[str, object]] = {}
    for raw in properties_payload:
        if not isinstance(raw, Mapping):
            continue
        identifier = str(raw.get("id") or raw.get("propertyNumber") or raw.get("accountNumber") or "")
        if identifier:
            by_id[identifier] = raw

    upgraded: list[dict[str, str]] = []
    for property_id in legacy_ids:
        raw = by_id.get(str(property_id))
        if not raw:
            _LOGGER.warning("Legacy property %s not returned by API; skipping", property_id)
            continue
        consumer = _extract_consumer_from_payload(raw)
        if not consumer:
            _LOGGER.warning("Legacy property %s has no electricity consumer number; skipping", property_id)
            continue
        name = str(
            raw.get("name")
            or raw.get("displayName")
            or (raw.get("address", {}) or {}).get("displayAddress")
            or f"Property {property_id}"
        ).replace("\n", ", ")
        upgraded.append(
            {
                PROPERTY_KEY_ID: str(property_id),
                PROPERTY_KEY_CONSUMER: consumer,
                PROPERTY_KEY_NAME: name,
            }
        )

    if upgraded:
        new_data = {
            **entry.data,
            CONF_SELECTED_PROPERTIES: upgraded,
        }
        hass.config_entries.async_update_entry(entry, data=new_data)
        _LOGGER.debug(
            "Upgraded Red Energy entry %s with %d properties", entry.entry_id, len(upgraded)
        )
    else:
        _LOGGER.error(
            "Failed to upgrade legacy Red Energy entry %s - no valid consumer numbers found",
            entry.entry_id,
        )

    return [_selected_property_from_dict(item) for item in upgraded]


def _selected_property_from_dict(payload: Mapping[str, object]) -> SelectedProperty:
    return SelectedProperty(
        property_id=str(payload.get(PROPERTY_KEY_ID, "")),
        consumer_number=str(payload.get(PROPERTY_KEY_CONSUMER, "")),
        name=str(payload.get(PROPERTY_KEY_NAME, "Red Energy")),
    )


def _extract_consumer_from_payload(raw: Mapping[str, object]) -> str | None:
    services = raw.get("services")
    if not isinstance(services, Iterable):
        return None
    for service in services:
        if not isinstance(service, Mapping):
            continue
        service_type = str(service.get("type") or service.get("serviceType") or "").lower()
        if service_type not in {"electricity", "elec"}:
            continue
        for key in ("consumerNumber", "consumer_number", "consumerNo"):
            value = service.get(key)
            if value:
                return str(value)
    return None
