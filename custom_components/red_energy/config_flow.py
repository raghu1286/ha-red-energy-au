"""Config flow for the Red Energy integration."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_CLIENT_ID, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv

from .api import RedEnergyAuthError, RedEnergyClient
from .const import (
    CONF_PROPERTY_IDS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MINIMUM_UPDATE_INTERVAL,
)


@dataclass(slots=True)
class _DiscoveredProperty:
    identifier: str
    name: str


def _normalise_property(raw: Mapping[str, Any]) -> _DiscoveredProperty | None:
    for key in ("id", "propertyNumber", "propertyId", "accountNumber", "accountId"):
        identifier = raw.get(key)
        if identifier:
            break
    else:
        return None

    for key in ("name", "displayName"):
        name = raw.get(key)
        if name:
            break
    else:
        address = raw.get("address") or {}
        display = address.get("displayAddress") or address.get("gentrackDisplayAddress")
        name = str(display).replace("\n", ", ") if display else f"Property {identifier}"

    return _DiscoveredProperty(identifier=str(identifier), name=str(name))


class RedEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle configuration of the integration."""

    VERSION = 1

    def __init__(self) -> None:
        self._stored_user_input: dict[str, Any] = {}
        self._properties: list[_DiscoveredProperty] = []
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_USERNAME): str,
                        vol.Required(CONF_PASSWORD): str,
                        vol.Required(CONF_CLIENT_ID): str,
                    }
                ),
            )

        client = RedEnergyClient(
            async_get_clientsession(self.hass),
            username=user_input[CONF_USERNAME],
            password=user_input[CONF_PASSWORD],
            client_id=user_input[CONF_CLIENT_ID],
        )

        if not await client.async_test_credentials():
            errors["base"] = "invalid_auth"
        else:
            try:
                properties = await client.async_get_properties()
            except RedEnergyAuthError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                discovered: list[_DiscoveredProperty] = []
                for raw in properties:
                    if not isinstance(raw, Mapping):
                        continue
                    prop = _normalise_property(raw)
                    if prop:
                        discovered.append(prop)

                if not discovered:
                    errors["base"] = "no_properties"
                else:
                    self._stored_user_input = user_input
                    self._properties = discovered
                    await self.async_set_unique_id(
                        f"{user_input[CONF_USERNAME].lower()}_{user_input[CONF_CLIENT_ID]}"
                    )
                    if not self._reauth_entry:
                        self._abort_if_unique_id_configured()
                    return await self.async_step_select_properties()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=user_input.get(CONF_USERNAME, "")): str,
                    vol.Required(CONF_PASSWORD, default=user_input.get(CONF_PASSWORD, "")): str,
                    vol.Required(CONF_CLIENT_ID, default=user_input.get(CONF_CLIENT_ID, "")): str,
                }
            ),
            errors=errors,
        )

    async def async_step_select_properties(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if not self._properties:
            return self.async_abort(reason="no_properties")

        choices = {prop.identifier: prop.name for prop in self._properties}

        if user_input is None:
            return self.async_show_form(
                step_id="select_properties",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_PROPERTY_IDS, default=list(choices.keys())): cv.multi_select(choices),
                    }
                ),
            )

        property_ids = cv.ensure_list(user_input[CONF_PROPERTY_IDS])
        if not property_ids:
            return self.async_show_form(
                step_id="select_properties",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_PROPERTY_IDS, default=list(choices.keys())): cv.multi_select(choices),
                    }
                ),
                errors={"base": "select_property"},
            )

        data = {**self._stored_user_input, CONF_PROPERTY_IDS: property_ids}

        if self._reauth_entry:
            self.hass.config_entries.async_update_entry(
                self._reauth_entry,
                data=data,
            )
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        title = self._properties[0].name if len(property_ids) == 1 else "Red Energy"
        return self.async_create_entry(title=title, data=data)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        entry_id = self.context.get("entry_id")
        if entry_id:
            self._reauth_entry = self.hass.config_entries.async_get_entry(entry_id)
        return await self.async_step_user(entry_data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return RedEnergyOptionsFlow(config_entry)


class RedEnergyOptionsFlow(config_entries.OptionsFlow):
    """Handle options for the integration."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(self, user_input: Mapping[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=dict(user_input))

        default_minutes = int(
            self._entry.options.get(
                CONF_UPDATE_INTERVAL,
                DEFAULT_UPDATE_INTERVAL.total_seconds() // 60,
            )
        )

        interval_options = {
            15: "15 minutes",
            30: "30 minutes",
            60: "60 minutes",
            120: "120 minutes",
        }

        minimum_minutes = int(MINIMUM_UPDATE_INTERVAL.total_seconds() / 60)
        if minimum_minutes not in interval_options:
            interval_options[minimum_minutes] = f"{minimum_minutes} minutes"

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_UPDATE_INTERVAL, default=int(default_minutes)): vol.In(interval_options),
                }
            ),
        )
