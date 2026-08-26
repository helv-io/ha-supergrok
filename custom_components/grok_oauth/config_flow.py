"""Config flow for SuperGrok OAuth."""

from __future__ import annotations

import secrets
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_LLM_HASS_API, CONF_NAME, CONF_PROMPT
from homeassistant.core import callback
from homeassistant.helpers import llm, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_ACCOUNT_EMAIL,
    CONF_ACCOUNT_ID,
    CONF_ACCOUNT_NAME,
    CONF_CHAT_MODEL,
    CONF_IMAGE_MODEL,
    CONF_MAX_TOKENS,
    CONF_SELECTED_MODELS,
    CONF_TEMPERATURE,
    DEFAULT_AI_TASK_NAME,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_MAX_TOKENS,
    DEFAULT_NAME,
    DOMAIN,
    LOGGER,
    OAUTH_REDIRECT_URI,
    SUBENTRY_TYPE_AI_TASK,
    SUBENTRY_TYPE_CONVERSATION,
)
from .models import (
    DEFAULT_SELECTED_MODELS,
    build_initial_subentries,
    chat_model_options,
    chat_models,
    first_image_model,
    image_model_options,
    picker_options,
    without_withheld_models,
)
from .oauth import (
    GrokOAuthError,
    account_unique_id,
    build_authorize_url,
    exchange_authorization_code,
    fetch_userinfo,
    generate_pkce,
    parse_authorization_callback,
    poll_device_token,
    request_device_authorization,
)


def _models_schema(defaults: list[str] | None = None) -> vol.Schema:
    selected = without_withheld_models(defaults or list(DEFAULT_SELECTED_MODELS))
    return vol.Schema(
        {
            vol.Required(
                CONF_SELECTED_MODELS,
                default=selected,
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
    VERSION = 2

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
                                    "label": "Device code (recommended: no paste, approve on any device)",
                                },
                                {
                                    "value": "browser",
                                    "label": "Browser login (backup: paste the localhost callback URL)",
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
            try:
                self._account = await fetch_userinfo(session, tokens.access_token)
            except Exception:  # noqa: BLE001
                LOGGER.warning("SuperGrok userinfo failed after device login; continuing")
                self._account = {}

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
        account_id = account_unique_id(self._account, self._tokens)
        if not account_id:
            LOGGER.warning("SuperGrok login succeeded but no account id was available")
            return self.async_abort(reason="oauth_error")
        await self.async_set_unique_id(account_id)

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
            selected = without_withheld_models(user_input.get(CONF_SELECTED_MODELS) or [])
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
            LOGGER.info("Creating SuperGrok OAuth entry for %s models=%s", title, selected)
            prompt = llm.DEFAULT_INSTRUCTIONS_PROMPT
            llm_apis = [llm.LLM_API_ASSIST]
            return self.async_create_entry(
                title=f"Grok ({title})" if title != DEFAULT_NAME else DEFAULT_NAME,
                data={
                    **(self._tokens or {}),
                    CONF_ACCOUNT_ID: self._account.get("sub"),
                    CONF_ACCOUNT_EMAIL: self._account.get("email"),
                    CONF_ACCOUNT_NAME: self._account.get("name"),
                    CONF_SELECTED_MODELS: selected,
                    CONF_LLM_HASS_API: llm_apis,
                    CONF_PROMPT: prompt,
                },
                subentries=build_initial_subentries(
                    selected, prompt=prompt, llm_apis=llm_apis
                ),
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

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return Conversation and AI Task subentries (OpenAI Conversation shape)."""
        return {
            SUBENTRY_TYPE_CONVERSATION: GrokSubentryFlowHandler,
            SUBENTRY_TYPE_AI_TASK: GrokSubentryFlowHandler,
        }


class GrokOAuthOptionsFlow(OptionsFlow):
    """Change which models / Voice / Imagine surfaces are exposed."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the multi-picker."""
        current = without_withheld_models(
            self.config_entry.options.get(
                CONF_SELECTED_MODELS,
                self.config_entry.data.get(CONF_SELECTED_MODELS, DEFAULT_SELECTED_MODELS),
            )
        )
        if user_input is not None:
            selected = without_withheld_models(user_input.get(CONF_SELECTED_MODELS) or [])
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
            return self.async_create_entry(title="", data={})

        return self.async_show_form(step_id="init", data_schema=_models_schema(current))


class GrokSubentryFlowHandler(ConfigSubentryFlow):
    """Add or reconfigure a Conversation agent or AI Task (OpenAI pattern)."""

    @property
    def _is_new(self) -> bool:
        """Return whether this flow is creating a subentry."""
        return self.source == "user"

    def _suggested(self) -> dict[str, Any]:
        """Defaults for a new subentry, or the current subentry data."""
        if not self._is_new:
            return dict(self._get_reconfigure_subentry().data)
        entry = self._get_entry()
        selected = without_withheld_models(
            entry.data.get(CONF_SELECTED_MODELS, DEFAULT_SELECTED_MODELS)
        )
        chats = chat_models(selected)
        suggested: dict[str, Any] = {
            CONF_CHAT_MODEL: chats[0] if chats else DEFAULT_CHAT_MODEL,
        }
        if self._subentry_type == SUBENTRY_TYPE_AI_TASK:
            if image := first_image_model(selected):
                suggested[CONF_IMAGE_MODEL] = image
            return suggested
        suggested[CONF_PROMPT] = entry.data.get(
            CONF_PROMPT, llm.DEFAULT_INSTRUCTIONS_PROMPT
        )
        suggested[CONF_LLM_HASS_API] = entry.data.get(
            CONF_LLM_HASS_API, [llm.LLM_API_ASSIST]
        )
        return suggested

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a subentry."""
        return await self.async_step_init(user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure an existing subentry, including the system prompt."""
        return await self.async_step_init(user_input)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Name, prompt, Control Home Assistant, and chat model."""
        if self._get_entry().state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        suggested = self._suggested()
        if user_input is not None:
            if not user_input.get(CONF_LLM_HASS_API):
                user_input.pop(CONF_LLM_HASS_API, None)
            if self._subentry_type == SUBENTRY_TYPE_CONVERSATION:
                user_input.setdefault(
                    CONF_PROMPT,
                    suggested.get(CONF_PROMPT, llm.DEFAULT_INSTRUCTIONS_PROMPT),
                )
            if self._is_new:
                return self.async_create_entry(
                    title=user_input.pop(CONF_NAME),
                    data=user_input,
                )
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                data=user_input,
            )

        hass_apis = [
            selector.SelectOptionDict(label=api.name, value=api.id)
            for api in llm.async_get_apis(self.hass)
        ]
        if suggested_llm := suggested.get(CONF_LLM_HASS_API):
            if isinstance(suggested_llm, str):
                suggested_llm = [suggested_llm]
            valid = {api.id for api in llm.async_get_apis(self.hass)}
            suggested[CONF_LLM_HASS_API] = [api for api in suggested_llm if api in valid]

        schema: dict[Any, Any] = {}
        if self._is_new:
            default_name = (
                DEFAULT_AI_TASK_NAME
                if self._subentry_type == SUBENTRY_TYPE_AI_TASK
                else DEFAULT_CONVERSATION_NAME
            )
            schema[vol.Required(CONF_NAME, default=default_name)] = str

        if self._subentry_type == SUBENTRY_TYPE_CONVERSATION:
            schema.update(
                {
                    vol.Optional(
                        CONF_PROMPT,
                        description={
                            "suggested_value": suggested.get(
                                CONF_PROMPT, llm.DEFAULT_INSTRUCTIONS_PROMPT
                            )
                        },
                    ): selector.TemplateSelector(),
                    vol.Optional(CONF_LLM_HASS_API): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=hass_apis, multiple=True)
                    ),
                }
            )

        live_models: list[str] = []
        client = getattr(self._get_entry(), "runtime_data", None)
        if client is not None and hasattr(client, "list_models"):
            try:
                live_models = await client.list_models()
            except Exception:  # noqa: BLE001
                live_models = []

        schema[
            vol.Required(
                CONF_CHAT_MODEL,
                default=suggested.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=chat_model_options(live_models),
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
        schema[vol.Optional(CONF_MAX_TOKENS, default=DEFAULT_MAX_TOKENS)] = (
            selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=256, max=16384, step=256, mode=selector.NumberSelectorMode.BOX
                )
            )
        )
        schema[vol.Optional(CONF_TEMPERATURE)] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=2, step=0.1, mode=selector.NumberSelectorMode.BOX
            )
        )

        if self._subentry_type == SUBENTRY_TYPE_AI_TASK:
            schema[
                vol.Optional(
                    CONF_IMAGE_MODEL,
                    description={
                        "suggested_value": suggested.get(CONF_IMAGE_MODEL),
                    },
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=image_model_options(live_models),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), suggested
            ),
        )
