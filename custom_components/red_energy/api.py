"""Client for interacting with the Red Energy cloud API."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import string
import uuid
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp
import async_timeout
from homeassistant.util import dt as dt_util

from .const import DEFAULT_UPDATE_INTERVAL
from .models import DailyUsageEntry

_LOGGER = logging.getLogger(__name__)

API_TIMEOUT = 30
_DATE_FMT = "%Y-%m-%d"


class RedEnergyError(Exception):
    """Base error raised by the Red Energy client."""


class RedEnergyAuthError(RedEnergyError):
    """Raised when authentication fails."""


class RedEnergyClient:
    """Small wrapper around the Red Energy HTTP API."""

    DISCOVERY_URL = "https://login.redenergy.com.au/oauth2/default/.well-known/openid-configuration"
    REDIRECT_URI = "au.com.redenergy://callback"
    BASE_API_URL = "https://selfservice.services.retail.energy/v1"
    OKTA_AUTH_URL = "https://redenergy.okta.com/api/v1/authn"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        username: str,
        password: str,
        client_id: str,
    ) -> None:
        """Initialise the client with credentials."""
        self._session = session
        self._username = username
        self._password = password
        self._client_id = client_id

        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expires: datetime | None = None

    async def async_test_credentials(self) -> bool:
        """Attempt to authenticate, returning True if credentials are valid."""
        try:
            await self.async_ensure_token()
        except RedEnergyAuthError as err:
            _LOGGER.debug("Credential validation failed: %s", err)
            return False
        except Exception:  # noqa: BLE001 - unexpected failure should surface to logs
            _LOGGER.exception("Unexpected error while validating Red Energy credentials")
            return False
        return True

    async def async_ensure_token(self) -> None:
        """Ensure the client has a valid access token."""
        if self._access_token and self._token_expires and dt_util.utcnow() < self._token_expires:
            return

        await self._async_authenticate()

    async def _async_authenticate(self) -> None:
        """Perform an OAuth authentication flow to obtain tokens."""
        _LOGGER.debug("Authenticating with Red Energy")

        session_token, _ = await self._async_get_session_token()
        discovery = await self._async_get_discovery_data()

        code_verifier = _generate_code_verifier()
        code_challenge = _generate_code_challenge(code_verifier)

        auth_code = await self._async_get_authorisation_code(
            discovery["authorization_endpoint"],
            session_token,
            code_challenge,
        )

        await self._async_exchange_code_for_tokens(
            discovery["token_endpoint"],
            auth_code,
            code_verifier,
        )

    async def _async_get_session_token(self) -> tuple[str, str | None]:
        payload = {
            "username": self._username,
            "password": self._password,
            "options": {
                "warnBeforePasswordExpired": False,
                "multiOptionalFactorEnroll": False,
            },
        }

        async with async_timeout.timeout(API_TIMEOUT):
            async with self._session.post(self.OKTA_AUTH_URL, json=payload) as response:
                data = await response.json()
                if response.status != 200 or data.get("status") != "SUCCESS":
                    message = data.get("errorSummary") or data.get("status") or "Unknown error"
                    raise RedEnergyAuthError(message)
        self._log_api_payload(
            "POST authn",
            {
                "status": data.get("status"),
                "expires_at": data.get("expiresAt"),
            },
        )
        return data["sessionToken"], data.get("expiresAt")

    async def _async_get_discovery_data(self) -> dict[str, Any]:
        async with async_timeout.timeout(API_TIMEOUT):
            async with self._session.get(self.DISCOVERY_URL) as response:
                response.raise_for_status()
                data = await response.json()
        self._log_api_payload("GET discovery", data)
        return data

    async def _async_get_authorisation_code(
        self,
        endpoint: str,
        session_token: str,
        code_challenge: str,
    ) -> str:
        state = str(uuid.uuid4())
        nonce = str(uuid.uuid4())
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": self.REDIRECT_URI,
            "scope": "openid profile offline_access",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            "nonce": nonce,
            "sessionToken": session_token,
        }

        async with async_timeout.timeout(API_TIMEOUT):
            async with self._session.get(
                f"{endpoint}?{urlencode(params)}", allow_redirects=False
            ) as response:
                location = response.headers.get("Location")
                if not location:
                    raise RedEnergyAuthError("Authorisation redirect missing location header")

        parsed = urlparse(location)
        query = parse_qs(parsed.query)
        if "code" not in query:
            error = query.get("error", ["unknown"])[0]
            description = query.get("error_description", [""])[0]
            raise RedEnergyAuthError(f"Authorisation failed: {error} {description}")
        return query["code"][0]

    async def _async_exchange_code_for_tokens(
        self,
        endpoint: str,
        code: str,
        verifier: str,
    ) -> None:
        payload = {
            "grant_type": "authorization_code",
            "client_id": self._client_id,
            "code": code,
            "redirect_uri": self.REDIRECT_URI,
            "code_verifier": verifier,
        }

        async with async_timeout.timeout(API_TIMEOUT):
            async with self._session.post(endpoint, data=payload) as response:
                data = await response.json()
                if response.status != 200:
                    message = data.get("error_description", "token exchange failed")
                    raise RedEnergyAuthError(message)

        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token")
        expires_in = int(data.get("expires_in", DEFAULT_UPDATE_INTERVAL.total_seconds()))
        self._token_expires = dt_util.utcnow() + timedelta(seconds=expires_in)
        self._log_api_payload(
            "POST token",
            {
                "expires_in": expires_in,
                "has_refresh": bool(self._refresh_token),
            },
        )

    async def async_get_properties(self) -> list[dict[str, Any]]:
        """Return the properties linked to the account."""
        await self.async_ensure_token()
        url = f"{self.BASE_API_URL}/properties"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        async with async_timeout.timeout(API_TIMEOUT):
            async with self._session.get(url, headers=headers) as response:
                response.raise_for_status()
                payload = await response.json()
        self._log_api_payload("GET properties", payload)
        if isinstance(payload, list):
            return payload
        return payload.get("properties", []) if isinstance(payload, dict) else []

    async def async_get_daily_usage_entries(
        self,
        consumer_number: str,
        *,
        end_date: date | None = None,
        days: int = 35,
    ) -> list[DailyUsageEntry]:
        """Fetch and normalise daily usage for the given consumer."""
        await self.async_ensure_token()
        end = end_date or dt_util.utcnow().date()
        start = end - timedelta(days=days - 1)

        url = f"{self.BASE_API_URL}/usage/daily"
        params = {
            "consumerNumber": consumer_number,
            "fromDate": start.strftime(_DATE_FMT),
            "toDate": end.strftime(_DATE_FMT),
        }
        headers = {"Authorization": f"Bearer {self._access_token}"}

        async with async_timeout.timeout(API_TIMEOUT):
            async with self._session.get(url, headers=headers, params=params) as response:
                response.raise_for_status()
                payload = await response.json()
        self._log_api_payload(
            "GET usage/daily",
            {
                "params": params,
                "payload": payload,
            },
        )
        return _normalise_daily_payload(payload)

    async def async_get_interval_usage(
        self,
        consumer_number: str,
        *,
        end_date: date | None = None,
        days: int = 2,
    ) -> list[dict[str, Any]]:
        """Return raw interval usage data for the consumer."""
        await self.async_ensure_token()
        end = end_date or dt_util.utcnow().date()
        start = end - timedelta(days=days - 1)

        url = f"{self.BASE_API_URL}/usage/interval"
        params = {
            "consumerNumber": consumer_number,
            "fromDate": start.strftime(_DATE_FMT),
            "toDate": end.strftime(_DATE_FMT),
        }
        headers = {"Authorization": f"Bearer {self._access_token}"}

        async with async_timeout.timeout(API_TIMEOUT):
            async with self._session.get(url, headers=headers, params=params) as response:
                response.raise_for_status()
                payload = await response.json()

        self._log_api_payload(
            "GET usage/interval",
            {
                "params": params,
                "payload": payload,
            },
        )
        return payload if isinstance(payload, list) else []

    async def async_refresh_token(self) -> None:
        """Refresh the access token using the current refresh token."""
        if not self._refresh_token:
            raise RedEnergyAuthError("Refresh token not available")

        discovery = await self._async_get_discovery_data()
        payload = {
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "refresh_token": self._refresh_token,
        }

        async with async_timeout.timeout(API_TIMEOUT):
            async with self._session.post(discovery["token_endpoint"], data=payload) as response:
                data = await response.json()
                if response.status != 200:
                    raise RedEnergyAuthError(data.get("error_description", "token refresh failed"))

        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        expires_in = int(data.get("expires_in", DEFAULT_UPDATE_INTERVAL.total_seconds()))
        self._token_expires = dt_util.utcnow() + timedelta(seconds=expires_in)
        self._log_api_payload(
            "POST token/refresh",
            {
                "expires_in": expires_in,
                "has_refresh": bool(self._refresh_token),
            },
        )

    def _log_api_payload(self, label: str, payload: Any) -> None:
        """Log API payloads with size limits to aid debugging."""
        try:
            serialised = json.dumps(payload, default=str)
        except (TypeError, ValueError):
            serialised = str(payload)

        if len(serialised) > 4000:
            serialised = f"{serialised[:4000]}... (truncated)"

        _LOGGER.debug("%s response: %s", label, serialised)


def _generate_code_verifier() -> str:
    alphabet = string.ascii_letters + string.digits + "-._~"
    return "".join(secrets.choice(alphabet) for _ in range(48))


def _generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _normalise_daily_payload(payload: Any) -> list[DailyUsageEntry]:
    """Convert the API payload into normalised daily usage entries."""
    if not isinstance(payload, list):
        _LOGGER.debug("Unexpected daily usage payload type: %s", type(payload))
        return []

    entries: list[DailyUsageEntry] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        day_string = item.get("usageDate") or item.get("date")
        if not day_string:
            continue
        try:
            day = datetime.strptime(day_string[:10], _DATE_FMT).date()
        except ValueError:
            _LOGGER.debug("Skipping usage entry with invalid date: %s", day_string)
            continue

        consumption = 0.0
        generation = 0.0
        consumption_cost = 0.0
        generation_value = 0.0

        if isinstance(item.get("halfHours"), list):
            for interval in item["halfHours"]:
                if not isinstance(interval, dict):
                    continue
                consumption += float(interval.get("consumptionKwh", 0) or 0)
                generation += float(interval.get("generationKwh", 0) or 0)
                consumption_cost += float(interval.get("consumptionDollarIncGst") or interval.get("consumptionDollar") or 0)
                generation_value += float(interval.get("generationDollar") or interval.get("generationDollarIncGst") or 0)
        else:
            consumption = float(item.get("consumptionKwh") or item.get("usage") or 0)
            generation = float(item.get("generationKwh") or item.get("generation") or 0)
            consumption_cost = float(item.get("consumptionDollarIncGst") or item.get("consumptionDollar") or 0)
            generation_value = float(item.get("generationDollar") or item.get("generationDollarIncGst") or 0)

        entries.append(
            DailyUsageEntry(
                day=day,
                consumption_kwh=round(consumption, 3),
                generation_kwh=round(generation, 3),
                consumption_cost=round(consumption_cost, 2),
                generation_value=round(generation_value, 2),
            )
        )

    entries.sort(key=lambda entry: entry.day)
    return entries
