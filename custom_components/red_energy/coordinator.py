"""DataUpdateCoordinator for Red Energy."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api import RedEnergyAPI, RedEnergyAPIError, RedEnergyAuthError
from .data_validation import (
    DataValidationError,
    validate_customer_data,
    validate_properties_data,
    validate_usage_data,
)
from .error_recovery import RedEnergyErrorRecoverySystem, ErrorType
from .performance import PerformanceMonitor, DataProcessor
from .const import (
    CONF_CLIENT_ID,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SERVICE_TYPE_ELECTRICITY,
    SERVICE_TYPE_GAS,
)

_LOGGER = logging.getLogger(__name__)


class RedEnergyDataCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Red Energy data."""

    def __init__(
        self,
        hass: HomeAssistant,
        username: str,
        password: str,
        client_id: str,
        selected_accounts: List[str],
        services: List[str],
    ) -> None:
        """Initialize the coordinator."""
        self.username = username
        self.password = password
        self.client_id = client_id
        self.selected_accounts = selected_accounts or []  # FIX: normalize
        self.services = services or []  # FIX: normalize

        # Stage 5 enhancements
        self._error_recovery = RedEnergyErrorRecoverySystem(hass)
        self._performance_monitor = PerformanceMonitor(hass)
        self._data_processor = DataProcessor(self._performance_monitor)
        self.update_failures = 0

        # API client
        session = async_get_clientsession(hass)
        self.api = RedEnergyAPI(session)

        self._customer_data: Optional[Dict[str, Any]] = None
        self._properties: List[Dict[str, Any]] = []

        # If DEFAULT_SCAN_INTERVAL is an int (seconds), this is correct. If it's already a timedelta, set directly.
        update_iv = timedelta(seconds=DEFAULT_SCAN_INTERVAL) if isinstance(DEFAULT_SCAN_INTERVAL, (int, float)) else DEFAULT_SCAN_INTERVAL

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_iv,
        )

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from Red Energy API."""
        # FIX: Normalize filters up-front
        selected_accounts_set = set(self.selected_accounts or [])
        enabled_services_set = set(self.services or [])

        _LOGGER.debug(
            "Starting data update - selected_accounts=%s, services=%s",
            list(selected_accounts_set) if selected_accounts_set else "(all)",
            list(enabled_services_set) if enabled_services_set else "(all)",
        )

        try:
            # Ensure we're authenticated
            if not self.api._access_token:
                _LOGGER.debug("Authenticating with Red Energy API")
                await self.api.authenticate(self.username, self.password, self.client_id)

            # Get customer and property data if not cached
            if not self._customer_data:
                raw_customer_data = await self.api.get_customer_data()
                self._customer_data = validate_customer_data(raw_customer_data)

                raw_properties = await self.api.get_properties()
                _LOGGER.debug("Raw properties from API: %s", raw_properties)
                self._properties = validate_properties_data(raw_properties)
                _LOGGER.debug("Validated properties: %s", self._properties)

            # Check for account ID mismatches and auto-correct
            actual_property_ids = [prop.get("id") for prop in self._properties if prop.get("id")]
            _LOGGER.debug("Actual property IDs from API: %s", actual_property_ids)

            if selected_accounts_set and not any(acc in actual_property_ids for acc in selected_accounts_set):
                _LOGGER.warning(
                    "Selected accounts %s don't match API property IDs %s",
                    list(selected_accounts_set), actual_property_ids
                )
                if len(actual_property_ids) == 1:
                    _LOGGER.info("Auto-correcting to use actual property ID: %s", actual_property_ids[0])
                    selected_accounts_set = {actual_property_ids[0]}
                    self.selected_accounts = [actual_property_ids[0]]  # keep config in sync

            # Fetch usage data for selected accounts and services
            usage_data: Dict[str, Any] = {}

            for property_data in self._properties:
                property_id = property_data.get("id")
                if not property_id:
                    continue

                # FIX: Only filter by account when a selection is provided
                is_selected = not selected_accounts_set or (property_id in selected_accounts_set)
                _LOGGER.debug("Checking property id=%s selected=%s", property_id, is_selected)
                if not is_selected:
                    _LOGGER.debug("Skipping unselected property %s", property_id)
                    continue

                property_services = property_data.get("services", [])
                property_usage: Dict[str, Any] = {}

                _LOGGER.debug("Property %s has services: %s", property_id, property_services)

                for service in property_services:
                    service_type = service.get("type")
                    consumer_number = service.get("consumer_number")
                    active = service.get("active", True)

                    _LOGGER.debug(
                        "Processing service: type=%s, consumer_number=%s, active=%s, enabled_services=%s",
                        service_type, consumer_number, active,
                        list(enabled_services_set) if enabled_services_set else "(all)"
                    )

                    if not consumer_number:
                        _LOGGER.warning("Service missing consumer_number: %s", service)
                        continue

                    # FIX: Only filter by service when a selection is provided
                    if enabled_services_set and service_type not in enabled_services_set:
                        _LOGGER.debug(
                            "Service type '%s' not in enabled services %s",
                            service_type, list(enabled_services_set)
                        )
                        continue

                    if not active:
                        _LOGGER.debug("Service %s is inactive", service_type)
                        continue

                    try:
                        # Get usage data for the last 30 days (UTC)
                        end_dt = dt_util.utcnow()  # FIX: HA UTC
                        start_dt = end_dt - timedelta(days=30)

                        _LOGGER.debug(
                            "Fetching usage data: consumer_number=%s, start=%s, end=%s",
                            consumer_number, start_dt.strftime('%Y-%m-%d'), end_dt.strftime('%Y-%m-%d')
                        )

                        raw_usage = await self.api.get_usage_data(consumer_number, start_dt, end_dt)

                        _LOGGER.debug(
                            "Raw usage data type: %s, keys: %s",
                            type(raw_usage),
                            list(raw_usage.keys()) if isinstance(raw_usage, dict) else "Not a dict"
                        )

                        # Validate usage data
                        validated_usage = validate_usage_data(
                            raw_usage,
                            consumer_number=consumer_number,
                            from_date=start_dt.strftime('%Y-%m-%d'),
                            to_date=end_dt.strftime('%Y-%m-%d')
                        )

                        _LOGGER.debug(
                            "Validated usage data: total_usage=%s, total_cost=%s",
                            validated_usage.get("total_usage"),
                            validated_usage.get("total_cost")
                        )

                        property_usage[service_type] = {
                            "consumer_number": consumer_number,
                            "usage_data": validated_usage,
                            "last_updated": end_dt.isoformat(),
                        }

                        _LOGGER.debug(
                            "Fetched %s usage data for property %s: %s total usage",
                            service_type,
                            property_id,
                            validated_usage.get("total_usage", 0)
                        )

                    except (RedEnergyAPIError, DataValidationError) as err:
                        _LOGGER.error(
                            "Failed to fetch/validate %s usage for property %s: %s",
                            service_type, property_id, err
                        )
                        # Don't fail the entire update for one service error; add placeholder entry
                        property_usage[service_type] = {
                            "consumer_number": consumer_number,
                            "usage_data": {
                                "total_usage": 0.0,
                                "total_cost": 0.0,
                                "usage_data": [],
                                "from_date": "",
                                "to_date": "",
                            },
                            "last_updated": dt_util.utcnow().isoformat(),  # FIX
                            "error": str(err),
                        }
                        continue

                _LOGGER.debug(
                    "Property %s processed: %d services included after filtering",
                    property_id, len(property_usage)
                )
                if property_usage:
                    usage_data[property_id] = {
                        "property": property_data,
                        "services": property_usage,
                    }
                else:
                    _LOGGER.warning("No services processed for property %s (after filtering/validation)", property_id)

            if not usage_data:
                _LOGGER.error("No usage data retrieved. Debug info follows:")
                _LOGGER.error("- Properties count: %d", len(self._properties))
                _LOGGER.error("- Selected accounts: %s", list(selected_accounts_set) if selected_accounts_set else "(all)")
                _LOGGER.error("- Enabled services: %s", list(enabled_services_set) if enabled_services_set else "(all)")

                if self._properties:
                    for prop in self._properties:
                        _LOGGER.error(
                            "- Property: id=%s, services=%s",
                            prop.get("id"),
                            [s.get("type") for s in prop.get("services", [])]
                        )

                raise UpdateFailed("No usage data retrieved for any configured services")

            return {
                "customer": self._customer_data,
                "properties": self._properties,
                "usage_data": usage_data,
                "last_update": dt_util.utcnow().isoformat(),  # FIX
            }

        except RedEnergyAuthError as err:
            _LOGGER.error("Authentication failed during update: %s", err)
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except RedEnergyAPIError as err:
            _LOGGER.error("API error during update: %s", err)
            raise UpdateFailed(f"API error: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error during update")
            raise UpdateFailed(f"Unexpected error: {err}") from err

    async def _bulk_update_data(self) -> Dict[str, Any]:
        """Handle bulk data updates for multiple accounts efficiently."""
        try:
            # Ensure authentication
            if not self.api._access_token:
                await self.api.authenticate(self.username, self.password, self.client_id)

            # Get base data if needed
            if not self._customer_data:
                raw_customer_data = await self.api.get_customer_data()
                self._customer_data = validate_customer_data(raw_customer_data)

                raw_properties = await self.api.get_properties()
                self._properties = validate_properties_data(raw_properties)

            # Use bulk processor for multiple accounts
            usage_data = await self._data_processor.batch_process_properties(
                {prop["id"]: {"property": prop, "services": {}} for prop in self._properties if prop.get("id") in (self.selected_accounts or [])},
                self.selected_accounts or [],
                self.services or []
            )

            # Fetch actual usage data concurrently
            usage_tasks = []
            for property_data in self._properties:
                property_id = property_data.get("id")
                if not property_id:
                    continue
                if self.selected_accounts and property_id not in set(self.selected_accounts):
                    continue

                task = asyncio.create_task(
                    self._fetch_property_usage(property_data),
                    name=f"fetch_usage_{property_id}"
                )
                usage_tasks.append((property_id, task))

            # Wait for all tasks with error handling
            final_usage_data: Dict[str, Any] = {}
            for property_id, task in usage_tasks:
                try:
                    property_usage = await task
                    if property_usage:
                        final_usage_data[property_id] = property_usage
                except Exception as err:
                    _LOGGER.error("Failed to fetch usage for property %s: %s", property_id, err)
                    continue

            if not final_usage_data:
                raise UpdateFailed("No usage data retrieved for any configured services")

            return {
                "customer": self._customer_data,
                "properties": self._properties,
                "usage_data": final_usage_data,
                "last_update": dt_util.utcnow().isoformat(),  # FIX
            }

        except Exception as err:
            await self._error_recovery.async_handle_error(
                err, ErrorType.COORDINATOR_UPDATE, {"coordinator": self}
            )
            raise

    async def _fetch_property_usage(self, property_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch usage data for a single property."""
        property_id = property_data.get("id")
        property_services = property_data.get("services", [])
        property_usage: Dict[str, Any] = {}

        enabled_services_set = set(self.services or [])

        for service in property_services:
            service_type = service.get("type")
            consumer_number = service.get("consumer_number")

            # FIX: Only filter by service when a selection is provided
            if not consumer_number or (enabled_services_set and service_type not in enabled_services_set):
                continue

            if not service.get("active", True):
                continue

            try:
                # Get usage data for the last 30 days (UTC)
                end_dt = dt_util.utcnow()  # FIX
                start_dt = end_dt - timedelta(days=30)

                raw_usage = await self.api.get_usage_data(consumer_number, start_dt, end_dt)

                validated_usage = validate_usage_data(
                    raw_usage,
                    consumer_number=consumer_number,
                    from_date=start_dt.strftime('%Y-%m-%d'),
                    to_date=end_dt.strftime('%Y-%m-%d')
                )

                property_usage[service_type] = {
                    "consumer_number": consumer_number,
                    "usage_data": validated_usage,
                    "last_updated": end_dt.isoformat(),
                }

            except Exception as err:
                await self._error_recovery.async_handle_error(
                    err, ErrorType.API_DATA_INVALID,
                    {"property_id": property_id, "service_type": service_type}
                )
                continue

        if property_usage:
            return {
                "property": property_data,
                "services": property_usage,
            }

        return None

    async def _fetch_usage_data_optimized(self) -> Dict[str, Any]:
        """Fetch usage data with performance optimizations."""
        usage_data: Dict[str, Any] = {}
        for property_data in self._properties:
            property_id = property_data.get("id")
            if not property_id:
                continue
            # FIX: Only filter when selection provided
            if self.selected_accounts and property_id not in set(self.selected_accounts):
                continue

            property_usage = await self._fetch_property_usage(property_data)
            if property_usage:
                usage_data[property_id] = property_usage

        return usage_data

    # ---------- Access helpers for sensors ----------

    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics for the coordinator."""
        return self._performance_monitor.get_performance_stats()

    def get_error_statistics(self) -> Dict[str, Any]:
        """Get error recovery statistics."""
        return self._error_recovery.get_error_statistics()

    async def async_refresh_credentials(
        self, username: str, password: str, client_id: str
    ) -> bool:
        """Refresh credentials and test authentication."""
        try:
            self.username = username
            self.password = password
            self.client_id = client_id

            # Clear cached auth token to force re-authentication
            self.api._access_token = None
            self.api._refresh_token = None
            self.api._token_expires = None

            success = await self.api.authenticate(username, password, client_id)
            if success:
                # Clear cached data to force refresh
                self._customer_data = None
                self._properties = []
                await self.async_refresh()

            return success

        except Exception as err:
            _LOGGER.error("Failed to refresh credentials: %s", err)
            return False

    async def async_update_account_selection(
        self, selected_accounts: List[str], services: List[str]
    ) -> None:
        """Update account and service selection."""
        self.selected_accounts = selected_accounts or []
        self.services = services or []
        await self.async_refresh()

    # --- Data accessors used by sensor entities ---

    def get_property_data(self, property_id: str) -> Optional[Dict[str, Any]]:
        """Get cached property data by ID."""
        if not self.data or "usage_data" not in self.data:
            return None
        return self.data["usage_data"].get(property_id)

    def get_service_usage(self, property_id: str, service_type: str) -> Optional[Dict[str, Any]]:
        """Get usage data for a specific property and service."""
        property_data = self.get_property_data(property_id)
        if not property_data:
            return None
        return property_data.get("services", {}).get(service_type)

    def get_latest_usage(self, property_id: str, service_type: str) -> Optional[float]:
        """Get the most recent usage value for a property and service."""
        service_data = self.get_service_usage(property_id, service_type)
        if not service_data or "usage_data" not in service_data:
            return None
        usage_data = service_data["usage_data"].get("usage_data", [])
        if not usage_data:
            return None
        return usage_data[-1].get("usage", 0.0)

    def get_total_cost(self, property_id: str, service_type: str) -> Optional[float]:
        """Get the total cost for a property and service."""
        service_data = self.get_service_usage(property_id, service_type)
        if not service_data or "usage_data" not in service_data:
            return None
        return service_data["usage_data"].get("total_cost", 0.0)

    def get_total_usage(self, property_id: str, service_type: str) -> Optional[float]:
        """Get the total usage for a property and service."""
        service_data = self.get_service_usage(property_id, service_type)
        if not service_data or "usage_data" not in service_data:
            return None
        return service_data["usage_data"].get("total_usage", 0.0)