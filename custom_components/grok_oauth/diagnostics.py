"""Redacted diagnostics for SuperGrok OAuth."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_CHAT_MODEL, CONF_SELECTED_MODELS
from .models import chat_models, first_image_model, has_realtime, has_voice

TO_REDACT = {
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "value",
    "account_email",
    "account_name",
    "email",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    selected = list(entry.data.get(CONF_SELECTED_MODELS) or [])
    client = getattr(entry, "runtime_data", None)
    payload: dict[str, Any] = {
        "title": entry.title,
        "unique_id": entry.unique_id,
        "account_id": entry.data.get("account_id"),
        "account_email": entry.data.get("account_email"),
        "selected_models": selected,
        "chat_models": chat_models(selected),
        "voice": has_voice(selected),
        "realtime": has_realtime(selected),
        "imagine": first_image_model(selected),
        "subentries": [
            {
                "type": subentry.subentry_type,
                "title": subentry.title,
                "chat_model": subentry.data.get(CONF_CHAT_MODEL),
            }
            for subentry in entry.subentries.values()
        ],
        "token_expires_at": entry.data.get("expires_at"),
    }
    if client is not None:
        payload["chat_base"] = getattr(client, "_chat_base", None)
        payload["media_base"] = getattr(client, "_media_base", None)
        payload["token_expired"] = client.tokens.is_expired()
        payload["recent_events"] = list(getattr(client, "recent_events", []))
    return async_redact_data(payload, TO_REDACT)
