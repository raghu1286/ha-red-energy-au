"""The Red Energy integration."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CLIENT_ID, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import RedEnergyClient
from .const import (
    CONF_PROPERTY_IDS,
    CONF_UPDATE_INTERVAL,
    DATA_CLIENT,
    DATA_COORDINATOR,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAXIMUM_UPDATE_INTERVAL,
    MINIMUM_UPDATE_INTERVAL,
    PLATFORMS,
)
from .coordinator import RedEnergyCoordinator


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

    update_interval = _resolve_update_interval(entry.options)

    coordinator = RedEnergyCoordinator(
        hass,
        client=client,
        property_ids=entry.data.get(CONF_PROPERTY_IDS),
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
    """Handle migration of older config entries."""
    version = entry.version
    if version == 1:
        return True

    if version < 1:
        hass.config_entries.async_update_entry(entry, version=1)
        return True

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
