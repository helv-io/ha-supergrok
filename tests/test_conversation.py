"""Conversation subentry prompt + legacy selected_models fallback.

Does not import conversation.py: that platform pulls in hassil, which the
existing suite avoids. The setup policy lives in helpers.conversation_agent_specs.
"""

from __future__ import annotations

from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.supergrok import async_migrate_entry
from custom_components.supergrok.const import (
    CONF_CHAT_MODEL,
    CONF_SELECTED_MODELS,
    DOMAIN,
    SUBENTRY_TYPE_AI_TASK,
    SUBENTRY_TYPE_CONVERSATION,
)
from custom_components.supergrok.models import config_option, conversation_agent_specs


def test_legacy_specs_and_entry_prompt_fallback() -> None:
    """Installs without subentries mint one agent per chat model and use entry prompt."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SELECTED_MODELS: ["grok-4.6", "grok-4.5", "voice"],
            CONF_PROMPT: "Be a pirate",
            CONF_LLM_HASS_API: [llm.LLM_API_ASSIST],
        },
    )
    specs = conversation_agent_specs(entry)
    assert [(subentry, model, default) for subentry, model, default in specs] == [
        (None, "grok-4.6", True),
        (None, "grok-4.5", False),
    ]
    assert config_option(entry, None, CONF_PROMPT) == "Be a pirate"
    assert config_option(entry, None, CONF_LLM_HASS_API) == [llm.LLM_API_ASSIST]


def test_subentry_specs_and_prompt_with_entry_fallback() -> None:
    """Subentry prompt wins; missing llm api falls back to the parent entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={
            CONF_SELECTED_MODELS: ["grok-4.6", "grok-4.5"],
            CONF_PROMPT: "Entry prompt",
            CONF_LLM_HASS_API: [llm.LLM_API_ASSIST],
        },
        subentries_data=[
            {
                "data": {
                    CONF_CHAT_MODEL: "grok-4.5",
                    CONF_PROMPT: "Subentry prompt",
                },
                "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                "title": "Custom Grok",
                "unique_id": None,
            }
        ],
    )
    specs = conversation_agent_specs(entry)
    assert len(specs) == 1
    subentry, model, default_agent = specs[0]
    assert subentry is not None
    assert model == "grok-4.5"
    assert default_agent is True
    assert config_option(entry, subentry, CONF_PROMPT) == "Subentry prompt"
    assert config_option(entry, subentry, CONF_LLM_HASS_API) == [llm.LLM_API_ASSIST]


async def test_migrate_v1_creates_conversation_subentries(hass: HomeAssistant) -> None:
    """v1 selected_models + entry prompt become conversation and AI Task subentries."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            CONF_SELECTED_MODELS: ["grok-4.6", "voice"],
            CONF_PROMPT: "Legacy prompt",
            CONF_LLM_HASS_API: [llm.LLM_API_ASSIST],
        },
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)
    assert entry.version == 2
    conversations = entry.get_subentries_of_type(SUBENTRY_TYPE_CONVERSATION)
    assert len(conversations) == 1
    assert conversations[0].data[CONF_CHAT_MODEL] == "grok-4.6"
    assert conversations[0].data[CONF_PROMPT] == "Legacy prompt"
    assert conversations[0].data[CONF_LLM_HASS_API] == [llm.LLM_API_ASSIST]
    assert len(entry.get_subentries_of_type(SUBENTRY_TYPE_AI_TASK)) == 1

    specs = conversation_agent_specs(entry)
    assert len(specs) == 1
    subentry, model, _default = specs[0]
    assert model == "grok-4.6"
    assert config_option(entry, subentry, CONF_PROMPT) == "Legacy prompt"
