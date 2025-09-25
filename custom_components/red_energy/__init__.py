"""The Red Energy integration."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import (
    CONF_CLIENT_ID,
    DATA_SELECTED_ACCOUNTS,
    DOMAIN,
    SERVICE_TYPE_ELECTRICITY,
)
from .coordinator import RedEnergyDataCoordinator
from .services import async_setup_services, async_unload_services

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle migration of config entry data when Home Assistant requests it."""
    from .config_migration import RedEnergyConfigMigrator

    _LOGGER.debug(
        "Home Assistant triggered migration for entry %s from version %s",
        entry.entry_id,
        entry.version,
    )

    migrator = RedEnergyConfigMigrator(hass)
    success = await migrator.async_migrate_config_entry(entry)

    if not success:
        _LOGGER.error(
            "Config entry migration reported failure for entry %s; continuing with best effort",
            entry.entry_id,
        )

    return success


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Red Energy from a config entry."""
    _LOGGER.debug("Setting up Red Energy integration for entry %s", entry.entry_id)

    # Extract configuration
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    client_id = entry.data[CONF_CLIENT_ID]
    selected_accounts = [str(x) for x in entry.data.get(DATA_SELECTED_ACCOUNTS, [])]
    services = entry.data.get("services", [SERVICE_TYPE_ELECTRICITY])

    _LOGGER.debug(
        "Configuration: username=%s, accounts(raw)=%s, services=%s",
        username, selected_accounts, services
    )

    # Import Stage 5 components dynamically to avoid circular imports
    from .config_migration import RedEnergyConfigMigrator
    from .state_manager import RedEnergyStateManager
    from .device_manager import RedEnergyDeviceManager

    # Check for config migration needs
    migrator = RedEnergyConfigMigrator(hass)
    migration_success = await migrator.async_migrate_config_entry(entry)
    if not migration_success:
        _LOGGER.error("Failed to migrate config entry %s", entry.entry_id)
        # Continue anyway - migration failure shouldn't block setup

    # Initialize Stage 5 components
    state_manager = RedEnergyStateManager(hass)
    await state_manager.async_load_states()

    device_manager = RedEnergyDeviceManager(hass, entry)

    # Create data coordinator
    coordinator = RedEnergyDataCoordinator(
        hass, username, password, client_id, selected_accounts, services
    )

    # Test initial data fetch
    try:
        await coordinator.async_config_entry_first_refresh()
    except UpdateFailed as err:
        _LOGGER.error("Failed to set up Red Energy integration: %s", err)
        raise ConfigEntryNotReady(f"Failed to connect to Red Energy: {err}") from err

    # -------- Normalize selected accounts to property IDs --------
    data = coordinator.data or {}
    props = data.get("properties", [])  # validated properties
    prop_ids: list[str] = []
    acct_to_prop: dict[str, str] = {}

    for p in props:
        pid = str(p.get("id"))
        acc = str(p.get("accountNumber") or p.get("account_number") or "")
        if pid:
            prop_ids.append(pid)
        if pid and acc:
            acct_to_prop[acc] = pid

    original_selected = selected_accounts[:]  # copy before resolving
    resolved_selected: list[str] = []

    if original_selected:
        for acc in original_selected:
            pid = acct_to_prop.get(acc)
            if pid:
                resolved_selected.append(pid)
            elif acc in prop_ids:  # user already provided a property id
                resolved_selected.append(acc)
            else:
                _LOGGER.warning(
                    "Configured account '%s' not found in account->property map; "
                    "known property_ids=%s account->property=%s",
                    acc, prop_ids, acct_to_prop
                )
        if not resolved_selected:
            # Fallback: if nothing matched, default to all properties
            resolved_selected = prop_ids.copy()
    else:
        # No explicit selection: default to all property IDs
        resolved_selected = prop_ids.copy()

    # Dedupe while preserving order
    resolved_selected = list(dict.fromkeys(resolved_selected))

    _LOGGER.info(
        "Account selection normalized. config_selected=%s -> property_ids=%s",
        original_selected, resolved_selected
    )
    # -------------------------------------------------------------

    # Set up devices using resolved property ids
    devices = await device_manager.async_setup_devices(
        coordinator.data, resolved_selected, services
    )

    # Store coordinator and Stage 5 components
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "username": username,
        "selected_accounts": resolved_selected,  # store PROPERTY IDs
        "services": services,
        "state_manager": state_manager,
        "device_manager": device_manager,
        "devices": devices,
    }

    _LOGGER.debug(
        "Storing entry for sensors: property_ids=%s services=%s | usage_data.keys()=%s",
        resolved_selected, services, list((coordinator.data or {}).get("usage_data", {}).keys())
    )

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Set up services (only once for the first entry)
    if len(hass.data[DOMAIN]) == 1:
        await async_setup_services(hass)

    _LOGGER.info(
        "Red Energy integration setup complete for %s accounts with %s services",
        len(resolved_selected),
        len(services)
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Red Energy integration for entry %s", entry.entry_id)
    
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        # Clean up stored data
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        
        # Clean up Stage 5 components
        if "state_manager" in entry_data:
            state_manager = entry_data["state_manager"]
            await state_manager.async_save_states()
        
        if "device_manager" in entry_data:
            device_manager = entry_data["device_manager"]
            await device_manager.async_cleanup_orphaned_entities()
        
        # Stop coordinator
        if "coordinator" in entry_data:
            coordinator = entry_data["coordinator"]
            # The coordinator will be garbage collected
            
        # Remove domain data if empty
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
            # Unload services when last integration is removed
            await async_unload_services(hass)
        
        _LOGGER.debug("Red Energy integration unloaded successfully")
    
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
