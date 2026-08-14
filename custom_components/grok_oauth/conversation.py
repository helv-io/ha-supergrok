"""Conversation agents for Grok OAuth."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_CHAT_MODEL,
    CONF_SELECTED_MODELS,
    DEFAULT_CHAT_MODEL,
    DEFAULT_REALTIME_NAME,
    DEFAULT_TTS_VOICE,
    DOMAIN,
    LOGGER,
    REALTIME_ENABLED,
)
from .helpers import async_run_chat_log
from .models import CATALOG_BY_ID, chat_models, has_realtime

if TYPE_CHECKING:
    from .client import GrokClient


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one conversation entity per selected chat model."""
    selected = config_entry.data.get(CONF_SELECTED_MODELS, [DEFAULT_CHAT_MODEL])
    chats = chat_models(selected)
    entities: list[conversation.ConversationEntity] = [
        GrokConversationEntity(config_entry, model, realtime=False, default_agent=(index == 0))
        for index, model in enumerate(chats)
    ]
    if REALTIME_ENABLED and has_realtime(selected):
        entities.append(
            GrokConversationEntity(
                config_entry,
                "grok-voice-latest",
                realtime=True,
                default_agent=not chats,
            )
        )
    if not entities:
        entities.append(
            GrokConversationEntity(config_entry, DEFAULT_CHAT_MODEL, realtime=False, default_agent=True)
        )
    LOGGER.debug("Setting up %s Grok conversation entities", len(entities))
    async_add_entities(entities)


class GrokConversationEntity(
    conversation.ConversationEntity,
    conversation.AbstractConversationAgent,
):
    """Grok conversation agent backed by SuperGrok OAuth."""

    _attr_has_entity_name = False
    _attr_supports_streaming = False

    def __init__(
        self, entry: ConfigEntry, model: str, *, realtime: bool, default_agent: bool = False
    ) -> None:
        self.entry = entry
        self._model = model
        self._realtime = realtime
        self._default_agent = default_agent
        self._attr_unique_id = f"{entry.entry_id}_{'realtime' if realtime else model}"
        catalog = CATALOG_BY_ID.get(model)
        self._attr_name = DEFAULT_REALTIME_NAME if realtime else (catalog.label if catalog else f"Grok {model}")
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=self._attr_name,
            manufacturer="xAI",
            model=model,
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        if entry.data.get(CONF_LLM_HASS_API):
            self._attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Grok is multilingual."""
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """Register as an Assist conversation agent."""
        await super().async_added_to_hass()
        if self._default_agent:
            conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the agent."""
        if self._default_agent:
            conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Send the chat log to Grok and return Assist's result."""
        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                self.entry.data.get(CONF_LLM_HASS_API),
                self.entry.data.get(CONF_PROMPT),
                user_input.extra_system_prompt,
            )
        except conversation.ConverseError as err:
            return err.as_conversation_result()

        client: GrokClient = self.entry.runtime_data
        model = self.entry.data.get(CONF_CHAT_MODEL, self._model)
        LOGGER.info(
            "Conversation %s model=%s realtime=%s chars=%s",
            self.entity_id,
            model,
            self._realtime,
            len(user_input.text or ""),
        )
        try:
            await async_run_chat_log(
                client=client,
                chat_log=chat_log,
                model=model,
                agent_id=self.entity_id,
                realtime=self._realtime,
                voice=DEFAULT_TTS_VOICE,
            )
        except Exception:
            LOGGER.exception(
                "Conversation %s failed model=%s realtime=%s",
                self.entity_id,
                model,
                self._realtime,
            )
            raise
        return conversation.async_get_result_from_chat_log(user_input, chat_log)
