"""DataUpdateCoordinator for Vwala Open Energie."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import VwalaApiClient, VwalaApiError
from .const import DOMAIN, UPDATE_INTERVAL_HOURS

_LOGGER = logging.getLogger(__name__)


class VwalaDistributionCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches electricity distribution costs from the Vwala Open Energie API."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api_client: VwalaApiClient,
        api_key: str,
        provider_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_distribution_{provider_id}",
            update_interval=timedelta(hours=UPDATE_INTERVAL_HOURS),
        )
        self.entry = entry
        self._api_client = api_client
        self._api_key = api_key
        self._provider_id = provider_id

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch current year's electricity distribution costs."""
        year = str(datetime.now().year)
        _LOGGER.debug(
            "Fetching electricity distribution costs for provider %s, year %s",
            self._provider_id,
            year,
        )
        try:
            data = await self._api_client.get_electricity_distribution_costs(
                self._api_key,
                self._provider_id,
                year,
            )
        except VwalaApiError as exc:
            raise UpdateFailed(
                f"Error fetching distribution costs for {self._provider_id}: {exc}"
            ) from exc

        _LOGGER.debug(
            "Received %d cost line items for provider %s",
            len(data.get("data", [])),
            self._provider_id,
        )
        return data


class VwalaExciseDutiesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches electricity excise duty rates from the Vwala Open Energie API.

    Response ``data`` array contains items with ``rateType`` of either
    ``"flat"`` (single ``rate`` field) or ``"tiered"`` (``tiers`` list).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api_client: VwalaApiClient,
        api_key: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_excise_{entry.entry_id}",
            update_interval=timedelta(hours=UPDATE_INTERVAL_HOURS),
        )
        self.entry = entry
        self._api_client = api_client
        self._api_key = api_key

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch current year's electricity excise duty rates."""
        year = str(datetime.now().year)
        _LOGGER.debug("Fetching electricity excise duties for year %s", year)
        try:
            data = await self._api_client.get_electricity_excise_duties(
                self._api_key,
                year,
            )
        except VwalaApiError as exc:
            raise UpdateFailed(
                f"Error fetching excise duties: {exc}"
            ) from exc

        _LOGGER.debug(
            "Received %d excise duty items", len(data.get("data", []))
        )
        return data
