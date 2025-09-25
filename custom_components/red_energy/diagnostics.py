"""Diagnostics support for the Red Energy integration."""
from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CLIENT_ID, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import DATA_COORDINATOR, DOMAIN

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD, CONF_CLIENT_ID}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """Return diagnostics for a config entry."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime[DATA_COORDINATOR]

    payload: dict[str, object] = {
        "properties": {},
        "last_update_success": coordinator.last_update_success,
    }

    for property_id, data in (coordinator.data or {}).items():
        payload["properties"][property_id] = {
            "name": data.name,
            "consumer_number": data.consumer_number,
            "daily": data.breakdown.daily.__dict__,
            "weekly": data.breakdown.weekly.__dict__,
            "monthly": data.breakdown.monthly.__dict__,
            "last_updated": data.last_updated.isoformat(),
        }

    payload["config"] = async_redact_data(dict(entry.data), TO_REDACT)

    return payload
