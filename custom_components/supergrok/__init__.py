"""SuperGrok OAuth: SuperGrok login for Home Assistant conversation, voice, and Imagine."""

from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path
from types import MappingProxyType

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT, Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from homeassistant.helpers import (
    config_validation as cv,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    llm,
    selector,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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
    SUBENTRY_TYPE_AI_TASK,
    SUBENTRY_TYPE_CONVERSATION,
)
from .models import (
    build_initial_subentries,
    chat_models,
    first_image_model,
    has_realtime,
    has_voice,
)
from .oauth import OAuthTokens, revoke_tokens

PLATFORMS = (Platform.AI_TASK, Platform.CONVERSATION, Platform.STT, Platform.TTS)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type GrokConfigEntry = ConfigEntry[GrokClient]


def _selected(entry: ConfigEntry) -> list[str]:
    return list(entry.data.get(CONF_SELECTED_MODELS, [DEFAULT_CHAT_MODEL]))


def _persist(hass: HomeAssistant, entry: ConfigEntry) -> Callable[[OAuthTokens], None]:
    def _write(tokens: OAuthTokens) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, **tokens.as_dict()})

    return _write


def _image_message(prompt: str, filenames: list[str] | None) -> dict:
    """Build a chat message, optionally attaching local image files."""
    if not filenames:
        return {"role": "user", "content": prompt}
    parts: list[dict] = [{"type": "text", "text": prompt}]
    for name in filenames:
        path = Path(name)
        if not path.is_file():
            raise ServiceValidationError(f"Image file not found: {name}")
        mime = "image/jpeg"
        suffix = path.suffix.lower()
        if suffix == ".png":
            mime = "image/png"
        elif suffix in (".webp",):
            mime = "image/webp"
        elif suffix in (".gif",):
            mime = "image/gif"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}"},
            }
        )
    return {"role": "user", "content": parts}


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services. Browser login uses the Grok CLI loopback, not My HA."""

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
            messages=[
                _image_message(call.data["prompt"], call.data.get("image_filename"))
            ],
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
                vol.Optional("image_filename"): vol.All(cv.ensure_list, [cv.string]),
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
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    LOGGER.info(
        "SuperGrok OAuth %s ready title=%s chat=%s voice=%s realtime=%s imagine=%s "
        "(enable debug logging for custom_components.supergrok to see request traces)",
        async_get_loaded_integration(hass, DOMAIN).version,
        entry.title,
        chat_models(selected),
        has_voice(selected),
        has_realtime(selected),
        first_image_model(selected),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GrokConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: GrokConfigEntry) -> None:
    """Revoke the SuperGrok refresh token. Removal still succeeds if revoke fails."""
    try:
        tokens = OAuthTokens.from_dict(dict(entry.data))
    except KeyError:
        return
    try:
        await revoke_tokens(async_get_clientsession(hass), tokens)
    except Exception:  # noqa: BLE001
        LOGGER.warning("Could not revoke SuperGrok tokens for %s", entry.entry_id)


async def async_update_options(hass: HomeAssistant, entry: GrokConfigEntry) -> None:
    """Reload when a conversation / AI Task subentry is reconfigured."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: GrokConfigEntry) -> bool:
    """Move v1 selected-model agents onto conversation / AI Task subentries."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        _migrate_v1_subentries(hass, entry)
        hass.config_entries.async_update_entry(entry, version=2)
        LOGGER.info("Migrated SuperGrok OAuth entry %s to conversation subentries", entry.entry_id)
    return True


def _migrate_v1_subentries(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create conversation + AI Task subentries from CONF_SELECTED_MODELS."""
    if entry.get_subentries_of_type(SUBENTRY_TYPE_CONVERSATION) or entry.get_subentries_of_type(
        SUBENTRY_TYPE_AI_TASK
    ):
        return

    selected = _selected(entry)
    prompt = entry.data.get(CONF_PROMPT, llm.DEFAULT_INSTRUCTIONS_PROMPT)
    llm_apis = entry.data.get(CONF_LLM_HASS_API, [llm.LLM_API_ASSIST])
    if isinstance(llm_apis, str):
        llm_apis = [llm_apis]

    for payload in build_initial_subentries(selected, prompt=prompt, llm_apis=llm_apis):
        data = payload["data"]
        unique_id = payload.get("unique_id")
        if not isinstance(data, dict):
            continue
        subentry = ConfigSubentry(
            data=MappingProxyType(data),
            subentry_type=str(payload["subentry_type"]),
            title=str(payload["title"]),
            unique_id=unique_id if isinstance(unique_id, str) else None,
        )
        hass.config_entries.async_add_subentry(entry, subentry)
        _remap_legacy_entity(hass, entry, subentry)


def _remap_legacy_entity(
    hass: HomeAssistant, entry: ConfigEntry, subentry: ConfigSubentry
) -> None:
    """Keep existing conversation / AI Task entities when unique ids change."""
    if subentry.subentry_type == SUBENTRY_TYPE_CONVERSATION:
        model = subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)
        old_unique_id = f"{entry.entry_id}_{model}"
        platform = "conversation"
    elif subentry.subentry_type == SUBENTRY_TYPE_AI_TASK:
        old_unique_id = f"{entry.entry_id}_ai_task"
        platform = "ai_task"
    else:
        return

    entity_registry = er.async_get(hass)
    entity_id = entity_registry.async_get_entity_id(platform, DOMAIN, old_unique_id)
    if entity_id is not None:
        entity_registry.async_update_entity(
            entity_id,
            config_subentry_id=subentry.subentry_id,
            new_unique_id=subentry.subentry_id,
        )

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, old_unique_id)})
    if device is not None:
        device_registry.async_update_device(
            device.id,
            new_identifiers={(DOMAIN, subentry.subentry_id)},
            new_config_subentry_id=subentry.subentry_id,
        )
