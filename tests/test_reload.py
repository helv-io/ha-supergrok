"""Token refreshes save tokens without a reload. Real changes still reload."""

from __future__ import annotations

from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState, ConfigSubentry
from homeassistant.const import CONF_PROMPT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.supergrok import async_setup_entry
from custom_components.supergrok.config_flow import (
    GrokOAuthConfigFlow,
    GrokOAuthOptionsFlow,
)
from custom_components.supergrok.const import (
    CONF_CHAT_MODEL,
    CONF_SELECTED_MODELS,
    DOMAIN,
    SUBENTRY_TYPE_CONVERSATION,
)
from custom_components.supergrok.oauth import OAuthTokens


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Grok",
        unique_id="acct-1",
        version=2,
        data={
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": 9999999999,
            "token_type": "Bearer",
            "account_id": "acct-1",
            "account_email": "grok@example.com",
            "account_name": "Grok User",
            CONF_SELECTED_MODELS: ["grok-4.6", "voice"],
        },
        subentries_data=[
            {
                "data": {CONF_CHAT_MODEL: "grok-4.6", CONF_PROMPT: "Be brief"},
                "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                "title": "Grok 4.6",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def loaded(hass: HomeAssistant):
    """Run async_setup_entry with platforms and the network mocked out."""
    entry = _entry(hass)
    with (
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch(
            "custom_components.supergrok.client.async_get_loaded_integration"
        ) as integration,
    ):
        integration.return_value.version = "test"
        assert await async_setup_entry(hass, entry)
    assert entry.runtime_data._client_version == "test"
    entry.mock_state(hass, ConfigEntryState.LOADED)
    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        yield entry, reload


async def test_token_refresh_does_not_reload(hass: HomeAssistant, loaded) -> None:
    """A refresh stores the new tokens, keeps the client and skips the reload."""
    entry, reload = loaded
    client = entry.runtime_data
    client._tokens = OAuthTokens(
        access_token="old-access", refresh_token="old-refresh", expires_at=1
    )
    fresh = OAuthTokens(
        access_token="new-access", refresh_token="new-refresh", expires_at=9999999999
    )
    with patch(
        "custom_components.supergrok.client.refresh_tokens",
        AsyncMock(return_value=fresh),
    ):
        assert await client.async_access_token() == "new-access"
    await hass.async_block_till_done()

    reload.assert_not_called()
    assert entry.runtime_data is client
    assert entry.data["access_token"] == "new-access"
    assert entry.data["refresh_token"] == "new-refresh"
    assert client._headers()["Authorization"] == "Bearer new-access"


async def test_model_change_reloads_once(hass: HomeAssistant, loaded) -> None:
    """Configure saves the new models and the entry reloads once."""
    entry, reload = loaded
    flow = GrokOAuthOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id
    result = await flow.async_step_init({CONF_SELECTED_MODELS: ["grok-4.6"]})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_SELECTED_MODELS] == ["grok-4.6"]
    reload.assert_awaited_once_with(entry.entry_id)


async def test_agent_change_reloads(hass: HomeAssistant, loaded) -> None:
    """Reconfiguring a conversation agent still reloads the entry."""
    entry, reload = loaded
    subentry = next(iter(entry.subentries.values()))
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, CONF_PROMPT: "Be chatty"}
    )
    await hass.async_block_till_done()
    reload.assert_awaited_once_with(entry.entry_id)


async def test_new_agent_reloads(hass: HomeAssistant, loaded) -> None:
    """Adding a conversation agent still reloads the entry."""
    entry, reload = loaded
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=MappingProxyType({CONF_CHAT_MODEL: "grok-4.6"}),
            subentry_type=SUBENTRY_TYPE_CONVERSATION,
            title="Second",
            unique_id=None,
        ),
    )
    await hass.async_block_till_done()
    reload.assert_awaited_once_with(entry.entry_id)


def _reauth_flow(hass: HomeAssistant, entry: MockConfigEntry) -> GrokOAuthConfigFlow:
    """A reauth flow that has just finished signing in to the same account."""
    flow = GrokOAuthConfigFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.context = {"source": SOURCE_REAUTH, "entry_id": entry.entry_id}
    flow._reauth_entry = entry
    flow._tokens = OAuthTokens(
        access_token="signed-in-access",
        refresh_token="signed-in-refresh",
        expires_at=9999999999,
    ).as_dict()
    flow._account = {"sub": "acct-1", "email": "grok@example.com", "name": "Grok User"}
    return flow


async def test_reauth_while_loaded_reloads_once(
    hass: HomeAssistant, loaded, caplog: pytest.LogCaptureFixture
) -> None:
    """Signing in again saves the tokens and the listener reloads once."""
    entry, reload = loaded
    result = await _reauth_flow(hass, entry)._async_after_login()
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["access_token"] == "signed-in-access"
    reload.assert_awaited_once_with(entry.entry_id)
    assert "update listener" not in caplog.text


async def test_reauth_after_failed_setup_reloads_once(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """With no listener after a failed setup, the flow reloads the entry itself."""
    entry = _entry(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_ERROR)
    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        result = await _reauth_flow(hass, entry)._async_after_login()
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["access_token"] == "signed-in-access"
    reload.assert_awaited_once_with(entry.entry_id)
    assert "update listener" not in caplog.text


async def test_configure_lists_live_models(hass: HomeAssistant, loaded) -> None:
    """Configure fetches the account's models each time it opens."""
    entry, _ = loaded
    client = MagicMock()
    client.list_models = AsyncMock(return_value=["grok-4.7"])
    entry.runtime_data = client
    flow = GrokOAuthOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id
    result = await flow.async_step_init()
    options = result["data_schema"].schema[CONF_SELECTED_MODELS].config["options"]
    assert "grok-4.7" in {item["value"] for item in options}
    client.list_models.assert_awaited_once_with(refresh=True)
