"""API client for the Vwala Open Energie API."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import BASE_URL

_LOGGER = logging.getLogger(__name__)


class VwalaApiError(Exception):
    """Generic API error."""


class VwalaAuthError(VwalaApiError):
    """Authentication / OTP error."""


class VwalaApiClient:
    """Async HTTP client wrapping the Vwala Open Energie REST API."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Authentication – OTP flow
    # ------------------------------------------------------------------

    async def send_otp(self, email: str) -> str:
        """Request an OTP code to be sent to *email*.

        Returns the ``methodId`` that must be passed to :meth:`verify_otp`.
        """
        _LOGGER.debug("Sending OTP to %s", email)
        resp = await self._request(
            "POST",
            "/auth/otp/send",
            json={"email": email},
        )
        return resp["methodId"]

    async def verify_otp(self, method_id: str, code: str) -> str:
        """Verify the OTP *code* obtained from :meth:`send_otp`.

        Returns the short-lived session JWT used to create API keys.
        Raises :exc:`VwalaAuthError` on bad/expired code.
        """
        _LOGGER.debug("Verifying OTP for methodId %s", method_id)
        try:
            resp = await self._request(
                "POST",
                "/auth/otp/verify",
                json={"methodId": method_id, "code": code},
            )
        except VwalaApiError as exc:
            raise VwalaAuthError("Invalid or expired OTP code") from exc
        return resp["sessionJwt"]

    # ------------------------------------------------------------------
    # API key management
    # ------------------------------------------------------------------

    async def create_api_key(self, session_jwt: str) -> str:
        """Create a new long-lived API key using the session JWT.

        Returns the raw API key (shown only once – must be stored securely).
        """
        _LOGGER.debug("Creating API key")
        resp = await self._request(
            "POST",
            "/api-keys",
            headers={"Authorization": f"Bearer {session_jwt}"},
            json={},
            expected_status=(200, 201),
        )
        return resp["key"]

    async def list_api_keys(self, session_jwt: str) -> list[dict[str, Any]]:
        """List all API keys associated with the verified email."""
        resp = await self._request(
            "GET",
            "/api-keys",
            headers={"Authorization": f"Bearer {session_jwt}"},
        )
        return resp.get("data", [])

    async def revoke_api_key(self, session_jwt: str, uuid: str) -> None:
        """Revoke an API key by UUID."""
        await self._request(
            "DELETE",
            f"/api-keys/{uuid}",
            headers={"Authorization": f"Bearer {session_jwt}"},
        )

    # ------------------------------------------------------------------
    # Distribution net providers
    # ------------------------------------------------------------------

    async def get_providers(
        self,
        api_key: str,
        postal_code: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return distribution net providers, optionally filtered by *postal_code*."""
        params: dict[str, str] = {}
        if postal_code:
            params["postalCode"] = postal_code

        resp = await self._request(
            "GET",
            "/v1/distribution-net-providers",
            headers={"x-api-key": api_key},
            params=params,
        )
        return resp.get("data", [])

    async def get_provider(self, api_key: str, provider_id: str) -> dict[str, Any]:
        """Return a single distribution net provider by ID."""
        return await self._request(
            "GET",
            f"/v1/distribution-net-providers/{provider_id}",
            headers={"x-api-key": api_key},
        )

    async def get_electricity_distribution_costs(
        self,
        api_key: str,
        provider_id: str,
        year: str,
    ) -> dict[str, Any]:
        """Return electricity distribution costs for a provider and year.

        Response structure::

            {
                "distributionNetProviderId": "...",
                "distributionNetProviderName": "...",
                "data": [
                    {
                        "id": "...",
                        "label": "...",
                        "values": {
                            "withDigitalMeter": 123.45,
                            "withAnalogMeter": 67.89
                        },
                        "unit": "€/year"
                    },
                    ...
                ]
            }
        """
        return await self._request(
            "GET",
            f"/v1/distribution-net-providers/{provider_id}/distribution-costs/electricity",
            headers={"x-api-key": api_key},
            params={"year": year},
        )

    async def get_gas_distribution_costs(
        self,
        api_key: str,
        provider_id: str,
        year: str,
    ) -> dict[str, Any]:
        """Return gas distribution costs for a provider and year."""
        return await self._request(
            "GET",
            f"/v1/distribution-net-providers/{provider_id}/distribution-costs/gas",
            headers={"x-api-key": api_key},
            params={"year": year},
        )

    async def get_electricity_excise_duties(
        self,
        api_key: str,
        year: str,
    ) -> dict[str, Any]:
        """Return electricity excise duty rates for *year*.

        Response structure (flat rate example)::

            {
                "data": [
                    {
                        "id": "bijdrage_energiefonds",
                        "label": "Bijdrage Energiefonds",
                        "unit": "€/kWh",
                        "rateType": "flat",
                        "rate": 0.001234
                    },
                    {
                        "id": "federale_bijzondere_accijns",
                        "label": "Federale bijzondere accijns",
                        "unit": "€/kWh",
                        "rateType": "tiered",
                        "tiers": [
                            {"maxKwh": 3000, "rate": 0.002},
                            {"rate": 0.001}
                        ]
                    },
                    ...
                ]
            }
        """
        return await self._request(
            "GET",
            "/v1/excise-duties/electricity",
            headers={"x-api-key": api_key},
            params={"year": year},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        json: Any = None,
        expected_status: tuple[int, ...] = (200,),
    ) -> Any:
        url = f"{BASE_URL}{path}"
        _LOGGER.debug("%s %s params=%s", method, url, params)

        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status not in expected_status:
                    body = await response.text()
                    _LOGGER.error(
                        "%s %s returned HTTP %s: %s",
                        method,
                        url,
                        response.status,
                        body,
                    )
                    raise VwalaApiError(
                        f"HTTP {response.status} from {path}: {body}"
                    )
                return await response.json()
        except aiohttp.ClientError as exc:
            raise VwalaApiError(f"Network error calling {path}: {exc}") from exc
