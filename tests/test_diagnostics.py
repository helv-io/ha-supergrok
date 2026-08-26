"""Diagnostics must stay token-free."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.supergrok.const import CONF_SELECTED_MODELS, DOMAIN
from custom_components.supergrok.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_redact_tokens(hass: HomeAssistant) -> None:
    """Access, refresh, and id tokens never appear in diagnostics."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Grok (user@example.com)",
        unique_id="acct-1",
        data={
            "access_token": "SECRET_ACCESS_TOKEN",
            "refresh_token": "SECRET_REFRESH_TOKEN",
            "id_token": "SECRET_ID_TOKEN",
            "expires_at": 1700000000,
            "account_id": "acct-1",
            "account_email": "user@example.com",
            CONF_SELECTED_MODELS: ["grok-4.6", "voice", "realtime"],
        },
    )
    entry.add_to_hass(hass)

    payload = await async_get_config_entry_diagnostics(hass, entry)
    dumped = str(payload)
    assert "SECRET_ACCESS_TOKEN" not in dumped
    assert "SECRET_REFRESH_TOKEN" not in dumped
    assert "SECRET_ID_TOKEN" not in dumped
    assert "user@example.com" not in dumped
    assert payload.get("account_email") in (None, "**REDACTED**", "REDACTED")
    assert payload["selected_models"] == ["grok-4.6", "voice", "realtime"]
    assert payload["realtime"] is False
    assert payload["token_expires_at"] == 1700000000
