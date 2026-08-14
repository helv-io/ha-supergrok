"""Config flow for Grok OAuth."""

from __future__ import annotations

import secrets
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    SOURCE_REAUTH,
)
from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT
from homeassistant.helpers import llm, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_ACCOUNT_EMAIL,
    CONF_ACCOUNT_ID,
    CONF_ACCOUNT_NAME,
    CONF_SELECTED_MODELS,
    DEFAULT_NAME,
    DOMAIN,
    LOGGER,
    OAUTH_REDIRECT_URI,
)
from .models import DEFAULT_SELECTED_MODELS, picker_options
from .oauth import (
    GrokOAuthError,
    build_authorize_url,
    exchange_authorization_code,
    fetch_userinfo,
    generate_pkce,
    parse_authorization_callback,
    poll_device_token,
    request_device_authorization,
)


def _models_schema(defaults: list[str] | None = None) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_SELECTED_MODELS,
                default=defaults or list(DEFAULT_SELECTED_MODELS),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=picker_options(),
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
        }
    )


class GrokOAuthConfigFlow(ConfigFlow, domain=DOMAIN):
    """SuperGrok login via device code, with loopback paste-callback backup."""

    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        self._tokens: dict[str, Any] | None = None
        self._account: dict[str, Any] = {}
        self._oauth_error: str | None = None
        self._reauth_entry: ConfigEntry | None = None
        self._code_verifier: str | None = None
        self._oauth_state: str | None = None
        self._authorize_url: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose device code (default) or browser paste-callback backup."""
        if user_input is not None:
            method = user_input.get("method") or "device"
            LOGGER.info("Starting SuperGrok login via %s", method)
            if method == "browser":
                return await self.async_step_browser()
            return await self.async_step_device()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("method", default="device"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {
                                    "value": "device",
                                    "label": "Device code (recommended — no paste, approve on any device)",
                                },
                                {
                                    "value": "browser",
                                    "label": "Browser login (backup — paste the localhost callback URL)",
                                },
                            ],
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            description_placeholders={"redirect_uri": OAUTH_REDIRECT_URI},
        )

    async def async_step_browser(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Authorization-code + PKCE using the registered Grok CLI loopback."""
        errors: dict[str, str] = {}
        if user_input is not None:
            code, state, error = parse_authorization_callback(
                user_input.get("callback_url") or ""
            )
            if error in ("access_denied", "authorization_denied"):
                return self.async_abort(reason="access_denied")
            if error:
                LOGGER.warning("SuperGrok browser callback error=%s", error)
                errors["base"] = "oauth_failed"
            elif not code:
                errors["base"] = "missing_code"
            elif (
                state
                and self._oauth_state
                and state != self._oauth_state
            ):
                errors["base"] = "state_mismatch"
            elif not self._code_verifier:
                errors["base"] = "oauth_failed"
            else:
                session = async_get_clientsession(self.hass)
                try:
                    tokens = await exchange_authorization_code(
                        session, code, self._code_verifier
                    )
                except GrokOAuthError as err:
                    LOGGER.warning("SuperGrok code exchange failed: %s", err.details)
                    if err.reason in ("access_denied", "tier_blocked"):
                        return self.async_abort(reason=err.reason)
                    errors["base"] = (
                        err.reason
                        if err.reason in ("cannot_connect", "oauth_failed")
                        else "oauth_failed"
                    )
                else:
                    self._tokens = tokens.as_dict()
                    self._account = await fetch_userinfo(session, tokens.access_token)
                    return await self._async_after_login()

        if not self._authorize_url:
            self._code_verifier, challenge = generate_pkce()
            self._oauth_state = secrets.token_urlsafe(24)
            self._authorize_url = build_authorize_url(
                code_challenge=challenge, state=self._oauth_state
            )
            LOGGER.info(
                "Starting SuperGrok browser login redirect_uri=%s", OAUTH_REDIRECT_URI
            )

        return self.async_show_form(
            step_id="browser",
            data_schema=vol.Schema({vol.Required("callback_url"): str}),
            errors=errors,
            description_placeholders={
                "authorize_url": self._authorize_url or "",
                "redirect_uri": OAUTH_REDIRECT_URI,
            },
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """RFC 8628 device-code login (recommended)."""
        session = async_get_clientsession(self.hass)
        try:
            device = await request_device_authorization(session)
        except GrokOAuthError as err:
            LOGGER.warning("Could not start SuperGrok device login: %s", err.details)
            return self.async_abort(reason=err.reason)

        async def _wait_for_approval() -> None:
            try:
                tokens = await poll_device_token(session, device)
            except GrokOAuthError as err:
                self._oauth_error = err.reason
                return
            except Exception:  # noqa: BLE001
                LOGGER.exception("SuperGrok device-code polling failed")
                self._oauth_error = "oauth_failed"
                return
            self._tokens = tokens.as_dict()
            self._account = await fetch_userinfo(session, tokens.access_token)

        self._oauth_error = None
        return self.async_show_progress(
            step_id="oauth_progress",
            progress_action="wait_for_authorization",
            description_placeholders={
                "url": device.verification_uri_complete,
                "verification_uri": device.verification_uri,
                "user_code": device.user_code,
            },
            progress_task=self.hass.async_create_task(_wait_for_approval()),
        )

    async def async_step_oauth_progress(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """HA only allows progress → progress_done from this step."""
        return self.async_show_progress_done(next_step_id="oauth_finish")

    async def async_step_oauth_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Continue after the device-code waiter finishes."""
        if self._oauth_error:
            return self.async_abort(reason=self._oauth_error)
        if not self._tokens:
            return self.async_abort(reason="oauth_timeout")
        return await self._async_after_login()

    async def _async_after_login(self) -> ConfigFlowResult:
        """Set unique id and either update reauth or show the model picker."""
        account_id = (
            self._account.get("sub")
            or self._account.get("email")
            or (self._tokens or {}).get("access_token", "")[:16]
        )
        await self.async_set_unique_id(str(account_id))

        if self.source == SOURCE_REAUTH or self._reauth_entry:
            entry = self._reauth_entry or self._get_reauth_entry()
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(
                entry,
                data_updates={
                    **(self._tokens or {}),
                    CONF_ACCOUNT_ID: self._account.get("sub"),
                    CONF_ACCOUNT_EMAIL: self._account.get("email"),
                    CONF_ACCOUNT_NAME: self._account.get("name"),
                },
            )

        self._abort_if_unique_id_configured()
        return await self.async_step_models()

    async def async_step_models(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Multi-select which Grok surfaces to expose to Home Assistant."""
        if user_input is not None:
            selected = user_input.get(CONF_SELECTED_MODELS) or []
            if not selected:
                return self.async_show_form(
                    step_id="models",
                    data_schema=_models_schema([]),
                    errors={"base": "select_model"},
                )
            title = (
                self._account.get("name")
                or self._account.get("email")
                or DEFAULT_NAME
            )
            LOGGER.info("Creating Grok OAuth entry for %s models=%s", title, selected)
            return self.async_create_entry(
                title=f"Grok ({title})" if title != DEFAULT_NAME else DEFAULT_NAME,
                data={
                    **(self._tokens or {}),
                    CONF_ACCOUNT_ID: self._account.get("sub"),
                    CONF_ACCOUNT_EMAIL: self._account.get("email"),
                    CONF_ACCOUNT_NAME: self._account.get("name"),
                    CONF_SELECTED_MODELS: selected,
                    CONF_LLM_HASS_API: [llm.LLM_API_ASSIST],
                    CONF_PROMPT: llm.DEFAULT_INSTRUCTIONS_PROMPT,
                },
            )

        return self.async_show_form(step_id="models", data_schema=_models_schema())

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Re-run SuperGrok login when the refresh token dies."""
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauthentication."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow for the model picker."""
        return GrokOAuthOptionsFlow()


class GrokOAuthOptionsFlow(OptionsFlow):
    """Change which models / Voice / Realtime / Imagine surfaces are exposed."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the multi-picker."""
        current = list(
            self.config_entry.options.get(
                CONF_SELECTED_MODELS,
                self.config_entry.data.get(CONF_SELECTED_MODELS, DEFAULT_SELECTED_MODELS),
            )
        )
        if user_input is not None:
            selected = user_input.get(CONF_SELECTED_MODELS) or []
            if not selected:
                return self.async_show_form(
                    step_id="init",
                    data_schema=_models_schema(current),
                    errors={"base": "select_model"},
                )
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_SELECTED_MODELS: selected},
            )
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return self.async_create_entry(title="", data={CONF_SELECTED_MODELS: selected})

        return self.async_show_form(step_id="init", data_schema=_models_schema(current))
