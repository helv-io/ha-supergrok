"""SuperGrok OAuth — SuperGrok login for Home Assistant conversation, voice, and Imagine."""

from __future__ import annotations

from collections.abc import Callable

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_entry_oauth2_flow, config_validation as cv, selector
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_loaded_integration

from .client import GrokClient
from .const import (
    CONF_CHAT_MODEL,
    CONF_IMAGE_MODEL,
    CONF_SELECTED_MODELS,
    DEFAULT_CHAT_MODEL,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_REALTIME_MODEL,
    DOMAIN,
    LOGGER,
    REALTIME_ENABLED,
    SERVICE_CREATE_REALTIME_SESSION,
    SERVICE_GENERATE_CONTENT,
    SERVICE_GENERATE_IMAGE,
)
from .models import chat_models, first_image_model, has_realtime, has_voice
from .oauth import GrokOAuth2Implementation, OAuthTokens

PLATFORMS = (Platform.AI_TASK, Platform.CONVERSATION, Platform.STT, Platform.TTS)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type GrokConfigEntry = ConfigEntry[GrokClient]


def _selected(entry: ConfigEntry) -> list[str]:
    return list(entry.data.get(CONF_SELECTED_MODELS, [DEFAULT_CHAT_MODEL]))


def _persist(hass: HomeAssistant, entry: ConfigEntry) -> Callable[[OAuthTokens], None]:
    def _write(tokens: OAuthTokens) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, **tokens.as_dict()})

    return _write


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services. Browser login uses the Grok CLI loopback, not My HA."""
    config_entry_oauth2_flow.async_register_implementation(
        hass, DOMAIN, GrokOAuth2Implementation(hass)
    )

    def _entry_from_call(call: ServiceCall) -> GrokConfigEntry:
        entry = hass.config_entries.async_get_entry(call.data["config_entry"])
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(f"Invalid SuperGrok OAuth config entry: {call.data['config_entry']}")
        return entry  # type: ignore[return-value]

    async def generate_content(call: ServiceCall) -> ServiceResponse:
        entry = _entry_from_call(call)
        client: GrokClient = entry.runtime_data
        selected = _selected(entry)
        model = call.data.get("model") or entry.data.get(CONF_CHAT_MODEL) or (
            chat_models(selected)[0] if chat_models(selected) else DEFAULT_CHAT_MODEL
        )
        result = await client.chat(
            model=model,
            messages=[{"role": "user", "content": call.data["prompt"]}],
        )
        return {"text": result.text, "model": model}

    async def generate_image(call: ServiceCall) -> ServiceResponse:
        entry = _entry_from_call(call)
        client: GrokClient = entry.runtime_data
        selected = _selected(entry)
        model = (
            call.data.get("model")
            or entry.data.get(CONF_IMAGE_MODEL)
            or first_image_model(selected)
            or DEFAULT_IMAGE_MODEL
        )
        images = await client.generate_image(
            prompt=call.data["prompt"],
            model=model,
            n=int(call.data.get("n") or 1),
            aspect_ratio=call.data.get("aspect_ratio"),
            resolution=call.data.get("resolution"),
            response_format=call.data.get("response_format") or "url",
        )
        payload = []
        for image in images:
            payload.append(
                {
                    "url": image.url,
                    "b64_json": image.b64_json,
                    "revised_prompt": image.revised_prompt,
                    "mime_type": image.mime_type,
                    "model": image.model,
                }
            )
        return {"images": payload, "model": model}

    async def create_realtime_session(call: ServiceCall) -> ServiceResponse:
        entry = _entry_from_call(call)
        if not has_realtime(_selected(entry)):
            raise ServiceValidationError("Realtime is not enabled on this SuperGrok OAuth entry")
        client: GrokClient = entry.runtime_data
        model = call.data.get("model") or DEFAULT_REALTIME_MODEL
        secret = await client.create_realtime_client_secret(
            model=model,
            expires_seconds=int(call.data.get("expires_seconds") or 600),
        )
        return {
            "value": secret.get("value"),
            "expires_at": secret.get("expires_at"),
            "websocket_url": f"wss://api.x.ai/v1/realtime?model={model}",
            "model": model,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_GENERATE_CONTENT,
        generate_content,
        schema=vol.Schema(
            {
                vol.Required("config_entry"): selector.ConfigEntrySelector({"integration": DOMAIN}),
                vol.Required("prompt"): cv.string,
                vol.Optional("model"): cv.string,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GENERATE_IMAGE,
        generate_image,
        schema=vol.Schema(
            {
                vol.Required("config_entry"): selector.ConfigEntrySelector({"integration": DOMAIN}),
                vol.Required("prompt"): cv.string,
                vol.Optional("model"): cv.string,
                vol.Optional("n", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=8)),
                vol.Optional("aspect_ratio"): vol.In(
                    ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "2:1", "1:2", "auto")
                ),
                vol.Optional("resolution"): vol.In(("1k", "2k")),
                vol.Optional("response_format", default="url"): vol.In(("url", "b64_json")),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    # Realtime is withheld from this release. Flip REALTIME_ENABLED to restore.
    if REALTIME_ENABLED:
        hass.services.async_register(
            DOMAIN,
            SERVICE_CREATE_REALTIME_SESSION,
            create_realtime_session,
            schema=vol.Schema(
                {
                    vol.Required("config_entry"): selector.ConfigEntrySelector(
                        {"integration": DOMAIN}
                    ),
                    vol.Optional("model", default=DEFAULT_REALTIME_MODEL): cv.string,
                    vol.Optional("expires_seconds", default=600): vol.All(
                        vol.Coerce(int), vol.Range(min=30, max=3600)
                    ),
                }
            ),
            supports_response=SupportsResponse.ONLY,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GrokConfigEntry) -> bool:
    """Set up SuperGrok OAuth from a config entry."""
    try:
        tokens = OAuthTokens.from_dict(dict(entry.data))
    except KeyError as err:
        raise ConfigEntryAuthFailed("SuperGrok OAuth tokens are missing") from err

    client = GrokClient(hass, tokens, persist=_persist(hass, entry))
    try:
        await client.async_access_token()
    except ConfigEntryAuthFailed:
        raise
    except Exception as err:
        raise ConfigEntryNotReady(f"Could not refresh SuperGrok session: {err}") from err

    entry.runtime_data = client
    selected = _selected(entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    LOGGER.info(
        "SuperGrok OAuth %s ready account=%s chat=%s voice=%s realtime=%s imagine=%s "
        "(enable debug logging for custom_components.grok_oauth to see request traces)",
        async_get_loaded_integration(hass, DOMAIN).version,
        entry.data.get("account_email") or entry.title,
        chat_models(selected),
        has_voice(selected),
        has_realtime(selected),
        first_image_model(selected),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GrokConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
