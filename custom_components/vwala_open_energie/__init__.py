"""Vwala Open Energie Home Assistant integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import VwalaApiClient, VwalaApiError
from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_API_KEY,
    CONF_PROVIDER_ID,
    CONF_PROVIDER_NAME,
    DATA_KEY_DISTRIBUTION,
    DATA_KEY_EXCISE,
)
from .coordinator import VwalaDistributionCoordinator, VwalaExciseDutiesCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Vwala Open Energie from a config entry."""
    api_key: str = entry.data[CONF_API_KEY]
    provider_id: str = entry.data[CONF_PROVIDER_ID]
    provider_name: str = entry.data[CONF_PROVIDER_NAME]

    _LOGGER.debug(
        "Setting up Vwala Open Energie entry for provider '%s' (%s)",
        provider_name,
        provider_id,
    )

    session = async_get_clientsession(hass)
    api_client = VwalaApiClient(session)

    distribution_coordinator = VwalaDistributionCoordinator(
        hass=hass,
        entry=entry,
        api_client=api_client,
        api_key=api_key,
        provider_id=provider_id,
    )

    excise_coordinator = VwalaExciseDutiesCoordinator(
        hass=hass,
        entry=entry,
        api_client=api_client,
        api_key=api_key,
    )

    try:
        await distribution_coordinator.async_config_entry_first_refresh()
        await excise_coordinator.async_config_entry_first_refresh()
    except Exception as exc:  # noqa: BLE001
        raise ConfigEntryNotReady(
            f"Cannot reach Vwala Open Energie API: {exc}"
        ) from exc

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_KEY_DISTRIBUTION: distribution_coordinator,
        DATA_KEY_EXCISE: excise_coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up resources."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)
