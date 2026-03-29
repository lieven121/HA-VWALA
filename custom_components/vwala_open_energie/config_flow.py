"""Config flow for Vwala Open Energie."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    SelectOptionDict,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import VwalaApiClient, VwalaApiError, VwalaAuthError
from .const import (
    DOMAIN,
    CONF_EMAIL,
    CONF_API_KEY,
    CONF_POSTAL_CODE,
    CONF_METER_TYPE,
    CONF_TARIFF_TYPE,
    CONF_PROVIDER_ID,
    CONF_PROVIDER_NAME,
    METER_TYPE_DIGITAL,
    METER_TYPE_ANALOG,
    TARIFF_TYPE_ENKELVOUDIG,
    TARIFF_TYPE_TWEEVOUDIG,
    TARIFF_TYPE_NACHTMETER,
    OTP_VALIDITY_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


def _api_client(hass) -> VwalaApiClient:
    return VwalaApiClient(async_get_clientsession(hass))


class VwalaOpenEnergieConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Multi-step config flow:

    Entry point (``user``) shows a menu:
      A. OTP path:  ``user`` → ``otp`` → ``meter_setup`` → [``select_provider``]
      B. API key path: ``api_key`` → ``meter_setup`` → [``select_provider``]
    """

    VERSION = 1

    def __init__(self) -> None:
        # Authentication state
        self._email: str | None = None
        self._method_id: str | None = None
        self._otp_sent_at: float | None = None
        self._session_jwt: str | None = None
        self._api_key: str | None = None

        # Meter / location state
        self._postal_code: str | None = None
        self._meter_type: str | None = None
        self._tariff_type: str | None = None

        # Provider state
        self._providers: list[dict[str, Any]] = []
        self._selected_provider: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Entry point – choose login method
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        return self.async_show_menu(
            step_id="user",
            menu_options=["email", "api_key"],
        )

    # ------------------------------------------------------------------
    # Path A – e-mail → send OTP
    # ------------------------------------------------------------------

    async def async_step_email(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip().lower()
            client = _api_client(self.hass)

            try:
                self._method_id = await client.send_otp(email)
                self._email = email
                self._otp_sent_at = datetime.now().timestamp()
                return await self.async_step_otp()
            except VwalaApiError:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="email",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.EMAIL)
                    ),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Path B – enter existing long-lived API key directly
    # ------------------------------------------------------------------

    async def async_step_api_key(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            # Quick sanity-check: try to list providers with the supplied key
            client = _api_client(self.hass)
            try:
                await client.get_providers(api_key)
            except VwalaApiError:
                errors[CONF_API_KEY] = "invalid_api_key"
            else:
                self._api_key = api_key
                return await self.async_step_meter_setup()

        return self.async_show_form(
            step_id="api_key",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Path A – OTP code → session JWT → create API key
    # ------------------------------------------------------------------

    async def async_step_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            # Check that the OTP has not obviously expired client-side
            elapsed = datetime.now().timestamp() - (self._otp_sent_at or 0)
            if elapsed > OTP_VALIDITY_SECONDS:
                errors["base"] = "otp_expired"
            else:
                code = user_input["otp_code"].strip()
                client = _api_client(self.hass)

                try:
                    self._session_jwt = await client.verify_otp(self._method_id, code)
                except VwalaAuthError:
                    errors["base"] = "invalid_otp"
                except VwalaApiError:
                    errors["base"] = "cannot_connect"
                else:
                    # OTP valid – create a long-lived API key
                    try:
                        self._api_key = await client.create_api_key(self._session_jwt)
                    except VwalaApiError:
                        errors["base"] = "api_key_failed"
                    else:
                        return await self.async_step_meter_setup()

        remaining = max(
            0,
            OTP_VALIDITY_SECONDS - int(datetime.now().timestamp() - (self._otp_sent_at or 0)),
        )

        return self.async_show_form(
            step_id="otp",
            data_schema=vol.Schema(
                {
                    vol.Required("otp_code"): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                }
            ),
            description_placeholders={
                "email": self._email or "",
                "remaining": str(remaining),
            },
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 3 – postal code + meter configuration
    # ------------------------------------------------------------------

    async def async_step_meter_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            postal_code = user_input[CONF_POSTAL_CODE].strip()

            if len(postal_code) != 4 or not postal_code.isdigit():
                errors[CONF_POSTAL_CODE] = "invalid_postal_code"
            else:
                self._postal_code = postal_code
                self._meter_type = user_input[CONF_METER_TYPE]
                self._tariff_type = user_input[CONF_TARIFF_TYPE]

                # Discover providers for this postal code
                client = _api_client(self.hass)
                try:
                    self._providers = await client.get_providers(
                        self._api_key, self._postal_code
                    )
                except VwalaApiError:
                    errors["base"] = "cannot_connect"
                else:
                    if not self._providers:
                        errors[CONF_POSTAL_CODE] = "no_providers"
                    elif len(self._providers) == 1:
                        # Only one provider → skip selection step
                        self._selected_provider = self._providers[0]
                        return self._create_entry()
                    else:
                        return await self.async_step_select_provider()

        return self.async_show_form(
            step_id="meter_setup",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_POSTAL_CODE): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Required(CONF_METER_TYPE, default=METER_TYPE_DIGITAL): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=METER_TYPE_DIGITAL,
                                    label="Digitale meter",
                                ),
                                SelectOptionDict(
                                    value=METER_TYPE_ANALOG,
                                    label="Analoge meter",
                                ),
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Required(CONF_TARIFF_TYPE, default=TARIFF_TYPE_ENKELVOUDIG): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=TARIFF_TYPE_ENKELVOUDIG,
                                    label="Enkelvoudig (één tarief)",
                                ),
                                SelectOptionDict(
                                    value=TARIFF_TYPE_TWEEVOUDIG,
                                    label="Tweevoudig (dag/nacht)",
                                ),
                                SelectOptionDict(
                                    value=TARIFF_TYPE_NACHTMETER,
                                    label="Exclusief nachtmeter",
                                ),
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 4 – select distribution net provider
    # ------------------------------------------------------------------

    async def async_step_select_provider(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            provider_id = user_input[CONF_PROVIDER_ID]
            match = next(
                (p for p in self._providers if p["id"] == provider_id), None
            )
            if match is None:
                errors[CONF_PROVIDER_ID] = "invalid_provider"
            else:
                self._selected_provider = match
                return self._create_entry()

        options = [
            SelectOptionDict(value=p["id"], label=p["name"])
            for p in self._providers
        ]

        return self.async_show_form(
            step_id="select_provider",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROVIDER_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            description_placeholders={"postal_code": self._postal_code or ""},
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Create the config entry
    # ------------------------------------------------------------------

    def _create_entry(self) -> config_entries.FlowResult:
        provider = self._selected_provider
        return self.async_create_entry(
            title=f"{provider['name']} – {self._postal_code}",
            data={
                CONF_EMAIL: self._email,
                CONF_API_KEY: self._api_key,
                CONF_POSTAL_CODE: self._postal_code,
                CONF_METER_TYPE: self._meter_type,
                CONF_TARIFF_TYPE: self._tariff_type,
                CONF_PROVIDER_ID: provider["id"],
                CONF_PROVIDER_NAME: provider["name"],
            },
        )
