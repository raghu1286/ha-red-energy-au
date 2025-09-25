"""DataUpdateCoordinator for Red Energy."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api import RedEnergyAPI, RedEnergyAPIError, RedEnergyAuthError
from .data_validation import (
    DataValidationError,
    validate_customer_data,
    validate_daily_usage_summary,
    validate_monthly_usage_summary,
    validate_properties_data,
    validate_usage_data,
)
from .error_recovery import RedEnergyErrorRecoverySystem, ErrorType
from .performance import PerformanceMonitor, DataProcessor
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
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
        self.selected_accounts = selected_accounts or []
        self.services = services or []

        # Diagnostics / helpers
        self._error_recovery = RedEnergyErrorRecoverySystem(hass)
        self._performance_monitor = PerformanceMonitor(hass)
        self._data_processor = DataProcessor(self._performance_monitor)
        self.update_failures = 0

        # API client
        session = async_get_clientsession(hass)
        self.api = RedEnergyAPI(session)

        self._customer_data: Optional[Dict[str, Any]] = None
        self._properties: List[Dict[str, Any]] = []

        update_iv = (
            timedelta(seconds=DEFAULT_SCAN_INTERVAL)
            if isinstance(DEFAULT_SCAN_INTERVAL, (int, float))
            else DEFAULT_SCAN_INTERVAL
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_iv,
        )

    # ---------- Debug helpers ----------

    @staticmethod
    def _summarize_series(series: list) -> str:
        if not series:
            return "[]"
        n = len(series)
        if n <= 3:
            return str(series)
        # show head/tail
        head = series[:2]
        tail = series[-1:]
        return f"[{head} ... {tail}] (count={n})"

    @staticmethod
    def _summarize_service_block(svc: dict) -> dict:
        ud = svc.get("usage_data", {})
        return {
            "consumer": svc.get("consumer_number"),
            "last_updated": svc.get("last_updated"),
            "total_usage": ud.get("total_usage"),
            "total_cost": ud.get("total_cost"),
            "total_generation": ud.get("total_generation"),
            "total_generation_value": ud.get("total_generation_value"),
            "from": ud.get("from_date"),
            "to": ud.get("to_date"),
            "daily(len)": len(ud.get("usage_data", [])),
        }

    def _build_debug_snapshot(self, usage_data: Dict[str, Any]) -> dict:
        snap: Dict[str, Any] = {"properties": {}}
        for pid, block in usage_data.items():
            services = block.get("services", {})
            snap["properties"][pid] = {
                "property_name": block.get("property", {}).get("name"),
                "services": {stype: self._summarize_service_block(svc) for stype, svc in services.items()},
            }
        return snap

    # ---------- Update flow ----------

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from Red Energy API."""
        selected_accounts_set = set(self.selected_accounts or [])
        enabled_services_set = set(self.services or [])

        _LOGGER.debug(
            "Update start | selected_accounts=%s | services=%s",
            list(selected_accounts_set) if selected_accounts_set else "(all)",
            list(enabled_services_set) if enabled_services_set else "(all)",
        )

        try:
            # Auth
            if not self.api._access_token:
                _LOGGER.debug("Authenticating with Red Energy API")
                await self.api.authenticate(self.username, self.password, self.client_id)

            # Base data
            if not self._customer_data:
                raw_customer_data = await self.api.get_customer_data()
                self._customer_data = validate_customer_data(raw_customer_data)

                raw_properties = await self.api.get_properties()
                _LOGGER.debug("API properties raw=%s", raw_properties)
                self._properties = validate_properties_data(raw_properties)
                _LOGGER.debug("API properties validated=%s", self._properties)

            actual_property_ids = [p.get("id") for p in self._properties if p.get("id")]
            _LOGGER.debug("Property IDs available=%s", actual_property_ids)

            if selected_accounts_set and not any(acc in actual_property_ids for acc in selected_accounts_set):
                _LOGGER.warning(
                    "Configured accounts %s not in API list %s",
                    list(selected_accounts_set),
                    actual_property_ids,
                )
                if len(actual_property_ids) == 1:
                    self.selected_accounts = [actual_property_ids[0]]
                    selected_accounts_set = {actual_property_ids[0]}
                    _LOGGER.info("Auto-corrected selected_accounts to %s", self.selected_accounts)

            usage_data: Dict[str, Any] = {}
            for prop in self._properties:
                pid = prop.get("id")
                if not pid:
                    continue

                if selected_accounts_set and pid not in selected_accounts_set:
                    _LOGGER.debug("Skip property %s (not selected)", pid)
                    continue

                svcs = prop.get("services", [])
                _LOGGER.debug("Property %s -> services=%s", pid, svcs)
                prop_usage: Dict[str, Any] = {}

                for svc in svcs:
                    stype = svc.get("type")
                    consumer = svc.get("consumer_number")
                    active = svc.get("active", True)

                    if not consumer:
                        _LOGGER.warning("Property %s service %s missing consumer_number", pid, stype)
                        continue
                    if enabled_services_set and stype not in enabled_services_set:
                        _LOGGER.debug("Skip service %s for %s (filtered)", stype, pid)
                        continue
                    if not active:
                        _LOGGER.debug("Skip service %s for %s (inactive)", stype, pid)
                        continue

                    try:
                        end_dt = dt_util.utcnow()
                        start_dt = end_dt - timedelta(days=30)
                        _LOGGER.debug(
                            "Fetch usage | pid=%s stype=%s consumer=%s period=%s..%s",
                            pid, stype, consumer, start_dt.date(), end_dt.date()
                        )

                        raw_usage = await self.api.get_usage_data(consumer, start_dt, end_dt)
                        _LOGGER.debug(
                            "Raw usage pid=%s stype=%s: type=%s keys=%s",
                            pid, stype, type(raw_usage), list(raw_usage.keys()) if isinstance(raw_usage, dict) else "N/A"
                        )

                        validated = validate_usage_data(
                            raw_usage,
                            consumer_number=consumer,
                            from_date=start_dt.strftime("%Y-%m-%d"),
                            to_date=end_dt.strftime("%Y-%m-%d"),
                        )

                        # Supplement with daily and monthly summaries for richer analytics
                        daily_summary: Dict[str, Any] = {}
                        monthly_summary: Dict[str, Any] = {}

                        try:
                            raw_daily = await self.api.get_daily_usage_summary(consumer, start_dt, end_dt)
                            daily_summary = validate_daily_usage_summary(raw_daily)
                        except Exception as err:
                            _LOGGER.debug(
                                "Daily summary fetch failed for pid=%s stype=%s: %s",
                                pid, stype, err,
                                exc_info=True,
                            )

                        try:
                            month_end = end_dt
                            month_start = month_end - timedelta(days=365)
                            raw_monthly = await self.api.get_monthly_usage_summary(consumer, month_start, month_end)
                            monthly_summary = validate_monthly_usage_summary(raw_monthly)
                        except Exception as err:
                            _LOGGER.debug(
                                "Monthly summary fetch failed for pid=%s stype=%s: %s",
                                pid, stype, err,
                                exc_info=True,
                            )

                        _LOGGER.debug(
                            "Validated usage pid=%s stype=%s: total_usage=%s total_cost=%s daily_len=%s",
                            pid, stype,
                            validated.get("total_usage"),
                            validated.get("total_cost"),
                            len(validated.get("usage_data", [])),
                        )

                        prop_usage[stype] = {
                            "consumer_number": consumer,
                            "usage_data": validated,
                            "daily_summary": daily_summary,
                            "monthly_summary": monthly_summary,
                            "last_updated": end_dt.isoformat(),
                        }

                    except (RedEnergyAPIError, DataValidationError) as err:
                        _LOGGER.error("Fetch/validate failed pid=%s stype=%s err=%s", pid, stype, err)
                        prop_usage[stype] = {
                            "consumer_number": consumer,
                            "usage_data": {
                                "total_usage": 0.0,
                                "total_cost": 0.0,
                                "usage_data": [],
                                "from_date": "",
                                "to_date": "",
                            },
                            "daily_summary": {},
                            "monthly_summary": {},
                            "last_updated": dt_util.utcnow().isoformat(),
                            "error": str(err),
                        }

                _LOGGER.debug("Property %s -> services_kept=%d", pid, len(prop_usage))
                if prop_usage:
                    usage_data[pid] = {"property": prop, "services": prop_usage}

            if not usage_data:
                _LOGGER.error(
                    "No usage data. props=%d sel=%s services=%s",
                    len(self._properties),
                    list(selected_accounts_set) if selected_accounts_set else "(all)",
                    list(enabled_services_set) if enabled_services_set else "(all)",
                )
                for p in self._properties:
                    _LOGGER.error(
                        "Prop id=%s svc_types=%s",
                        p.get("id"),
                        [s.get("type") for s in p.get("services", [])],
                    )
                raise UpdateFailed("No usage data retrieved for any configured services")

            # Final snapshot: compact per-property/service totals
            snap = self._build_debug_snapshot(usage_data)
            _LOGGER.debug("Coordinator snapshot (compact): %s", snap)

            return {
                "customer": self._customer_data,
                "properties": self._properties,
                "usage_data": usage_data,
                "last_update": dt_util.utcnow().isoformat(),
            }

        except RedEnergyAuthError as err:
            _LOGGER.error("Authentication failed: %s", err)
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except RedEnergyAPIError as err:
            _LOGGER.error("API error during update: %s", err)
            raise UpdateFailed(f"API error: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error during update")
            raise UpdateFailed(f"Unexpected error: {err}") from err

    # ---------- Optional bulk paths (unchanged except UTC + guards) ----------

    async def _bulk_update_data(self) -> Dict[str, Any]:
        """Handle bulk data updates for multiple accounts efficiently."""
        try:
            if not self.api._access_token:
                await self.api.authenticate(self.username, self.password, self.client_id)

            if not self._customer_data:
                raw_customer_data = await self.api.get_customer_data()
                self._customer_data = validate_customer_data(raw_customer_data)

                raw_properties = await self.api.get_properties()
                self._properties = validate_properties_data(raw_properties)

            usage_data = await self._data_processor.batch_process_properties(
                {
                    prop["id"]: {"property": prop, "services": {}}
                    for prop in self._properties
                    if prop.get("id") in (self.selected_accounts or [])
                },
                self.selected_accounts or [],
                self.services or [],
            )

            tasks = []
            for prop in self._properties:
                pid = prop.get("id")
                if not pid:
                    continue
                if self.selected_accounts and pid not in set(self.selected_accounts):
                    continue
                tasks.append((pid, asyncio.create_task(self._fetch_property_usage(prop), name=f"fetch_usage_{pid}")))

            final_usage_data: Dict[str, Any] = {}
            for pid, task in tasks:
                try:
                    prop_usage = await task
                    if prop_usage:
                        final_usage_data[pid] = prop_usage
                except Exception as err:
                    _LOGGER.error("Failed to fetch usage for property %s: %s", pid, err)

            if not final_usage_data:
                raise UpdateFailed("No usage data retrieved for any configured services")

            _LOGGER.debug("Bulk snapshot: %s", self._build_debug_snapshot(final_usage_data))

            return {
                "customer": self._customer_data,
                "properties": self._properties,
                "usage_data": final_usage_data,
                "last_update": dt_util.utcnow().isoformat(),
            }

        except Exception as err:
            await self._error_recovery.async_handle_error(
                err, ErrorType.COORDINATOR_UPDATE, {"coordinator": self}
            )
            raise

    async def _fetch_property_usage(self, property_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch usage data for a single property."""
        pid = property_data.get("id")
        svcs = property_data.get("services", [])
        prop_usage: Dict[str, Any] = {}

        enabled_services_set = set(self.services or [])

        for svc in svcs:
            stype = svc.get("type")
            consumer = svc.get("consumer_number")

            if not consumer or (enabled_services_set and stype not in enabled_services_set):
                _LOGGER.debug("Skip property=%s service=%s (filtered/missing consumer)", pid, stype)
                continue
            if not svc.get("active", True):
                _LOGGER.debug("Skip property=%s service=%s (inactive)", pid, stype)
                continue

            try:
                end_dt = dt_util.utcnow()
                start_dt = end_dt - timedelta(days=30)

                raw = await self.api.get_usage_data(consumer, start_dt, end_dt)
                validated = validate_usage_data(
                    raw,
                    consumer_number=consumer,
                    from_date=start_dt.strftime("%Y-%m-%d"),
                    to_date=end_dt.strftime("%Y-%m-%d"),
                )

                prop_usage[stype] = {
                    "consumer_number": consumer,
                    "usage_data": validated,
                    "last_updated": end_dt.isoformat(),
                }

                _LOGGER.debug(
                    "Prop=%s service=%s fetched: totals u=%s c=%s days=%s",
                    pid, stype,
                    validated.get("total_usage"),
                    validated.get("total_cost"),
                    len(validated.get("usage_data", [])),
                )

            except Exception as err:
                await self._error_recovery.async_handle_error(
                    err, ErrorType.API_DATA_INVALID, {"property_id": pid, "service_type": stype}
                )

        if prop_usage:
            return {"property": property_data, "services": prop_usage}
        return None

    async def _fetch_usage_data_optimized(self) -> Dict[str, Any]:
        """Fetch usage data with performance optimizations."""
        usage_data: Dict[str, Any] = {}
        for prop in self._properties:
            pid = prop.get("id")
            if not pid:
                continue
            if self.selected_accounts and pid not in set(self.selected_accounts):
                continue
            block = await self._fetch_property_usage(prop)
            if block:
                usage_data[pid] = block
        return usage_data

    # ---------- Access helpers for sensors (with DEBUG) ----------

    def get_property_data(self, property_id: str) -> Optional[Dict[str, Any]]:
        """Get cached property data by ID."""
        if not self.data or "usage_data" not in self.data:
            _LOGGER.debug("get_property_data(%s): coordinator.data missing/empty", property_id)
            return None
        block = self.data["usage_data"].get(property_id)
        if block is None:
            _LOGGER.debug("get_property_data(%s): not found in usage_data keys=%s", property_id, list(self.data["usage_data"].keys()))
        else:
            _LOGGER.debug("get_property_data(%s): found services=%s", property_id, list(block.get("services", {}).keys()))
        return block

    def get_service_usage(self, property_id: str, service_type: str) -> Optional[dict]:
        """Get usage data for a specific property and service."""
        prop = self.get_property_data(property_id)
        if not prop:
            _LOGGER.debug("get_service_usage(%s,%s): property not found", property_id, service_type)
            return None
        svc = prop.get("services", {}).get(service_type)
        if svc is None:
            _LOGGER.debug(
                "get_service_usage(%s,%s): service missing; available=%s",
                property_id, service_type, list(prop.get("services", {}).keys())
            )
        else:
            ud = svc.get("usage_data", {})
            _LOGGER.debug(
                "get_service_usage(%s,%s): totals u=%s c=%s days=%s last=%s",
                property_id, service_type,
                ud.get("total_usage"), ud.get("total_cost"),
                len(ud.get("usage_data", [])), svc.get("last_updated")
            )
        return svc

    def get_latest_usage(self, property_id: str, service_type: str) -> Optional[float]:
        """Get the most recent usage value for a property and service."""
        svc = self.get_service_usage(property_id, service_type)
        if not svc or "usage_data" not in svc:
            _LOGGER.debug("get_latest_usage(%s,%s): no svc/usage_data", property_id, service_type)
            return None
        series = svc["usage_data"].get("usage_data", [])
        if not series:
            _LOGGER.debug("get_latest_usage(%s,%s): empty series", property_id, service_type)
            return None
        val = series[-1].get("usage", 0.0)
        _LOGGER.debug("get_latest_usage(%s,%s): %s", property_id, service_type, val)
        return val

    def get_total_cost(self, property_id: str, service_type: str) -> Optional[float]:
        """Get the total cost for a property and service."""
        svc = self.get_service_usage(property_id, service_type)
        if not svc or "usage_data" not in svc:
            _LOGGER.debug("get_total_cost(%s,%s): no svc/usage_data", property_id, service_type)
            return None
        val = svc["usage_data"].get("total_cost", 0.0)
        _LOGGER.debug("get_total_cost(%s,%s): %s", property_id, service_type, val)
        return val

    def get_total_usage(self, property_id: str, service_type: str) -> Optional[float]:
        """Get the total usage for a property and service."""
        svc = self.get_service_usage(property_id, service_type)
        if not svc or "usage_data" not in svc:
            _LOGGER.debug("get_total_usage(%s,%s): no svc/usage_data", property_id, service_type)
            return None
        val = svc["usage_data"].get("total_usage", 0.0)
        _LOGGER.debug("get_total_usage(%s,%s): %s", property_id, service_type, val)
        return val

    def get_total_generation(self, property_id: str, service_type: str) -> Optional[float]:
        """Get the total solar generation for a property and service."""
        svc = self.get_service_usage(property_id, service_type)
        if not svc or "usage_data" not in svc:
            _LOGGER.debug("get_total_generation(%s,%s): no svc/usage_data", property_id, service_type)
            return None
        val = svc["usage_data"].get("total_generation", 0.0)
        _LOGGER.debug("get_total_generation(%s,%s): %s", property_id, service_type, val)
        return val

    def get_total_generation_value(self, property_id: str, service_type: str) -> Optional[float]:
        """Get the monetary value of solar generation for a property and service."""
        svc = self.get_service_usage(property_id, service_type)
        if not svc or "usage_data" not in svc:
            _LOGGER.debug("get_total_generation_value(%s,%s): no svc/usage_data", property_id, service_type)
            return None
        val = svc["usage_data"].get("total_generation_value", 0.0)
        _LOGGER.debug("get_total_generation_value(%s,%s): %s", property_id, service_type, val)
        return val

    def get_daily_summary(self, property_id: str, service_type: str) -> Optional[Dict[str, Any]]:
        """Get daily summary data for a property/service."""
        svc = self.get_service_usage(property_id, service_type)
        if not svc:
            return None
        summary = svc.get("daily_summary")
        if not summary:
            _LOGGER.debug("get_daily_summary(%s,%s): summary missing", property_id, service_type)
            return None
        return summary

    def get_latest_daily_entry(self, property_id: str, service_type: str) -> Optional[Dict[str, Any]]:
        """Get the latest daily summary entry."""
        summary = self.get_daily_summary(property_id, service_type)
        if not summary:
            return None
        latest = summary.get("latest") or {}
        if not latest:
            entries = summary.get("entries", [])
            latest = entries[-1] if entries else {}
        return latest if latest else None

    def get_monthly_summary(self, property_id: str, service_type: str) -> Optional[Dict[str, Any]]:
        """Get monthly summary data for a property/service."""
        svc = self.get_service_usage(property_id, service_type)
        if not svc:
            return None
        summary = svc.get("monthly_summary")
        if not summary:
            _LOGGER.debug("get_monthly_summary(%s,%s): summary missing", property_id, service_type)
            return None
        return summary

    def get_latest_monthly_entry(self, property_id: str, service_type: str) -> Optional[Dict[str, Any]]:
        """Get the most recent monthly summary entry."""
        summary = self.get_monthly_summary(property_id, service_type)
        if not summary:
            return None
        latest = summary.get("latest") or {}
        if not latest:
            entries = summary.get("entries", [])
            latest = entries[-1] if entries else {}
        return latest if latest else None

    # ---------- Misc ----------

    def get_performance_metrics(self) -> Dict[str, Any]:
        return self._performance_monitor.get_performance_stats()

    def get_error_statistics(self) -> Dict[str, Any]:
        return self._error_recovery.get_error_statistics()

    async def async_refresh_credentials(self, username: str, password: str, client_id: str) -> bool:
        try:
            self.username = username
            self.password = password
            self.client_id = client_id
            self.api._access_token = None
            self.api._refresh_token = None
            self.api._token_expires = None
            ok = await self.api.authenticate(username, password, client_id)
            if ok:
                self._customer_data = None
                self._properties = []
                await self.async_refresh()
            return ok
        except Exception as err:
            _LOGGER.error("Failed to refresh credentials: %s", err)
            return False

    async def async_update_account_selection(self, selected_accounts: List[str], services: List[str]) -> None:
        self.selected_accounts = selected_accounts or []
        self.services = services or []
        await self.async_refresh()
