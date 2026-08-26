"""Conversation agents for SuperGrok OAuth."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_SELECTED_MODELS,
    CONF_TEMPERATURE,
    DEFAULT_CHAT_MODEL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_REALTIME_NAME,
    DEFAULT_TTS_VOICE,
    DOMAIN,
    LOGGER,
    REALTIME_ENABLED,
)
from .helpers import async_run_chat_log
from .models import CATALOG_BY_ID, config_option, conversation_agent_specs, has_realtime

PARALLEL_UPDATES = 0

if TYPE_CHECKING:
    from .client import GrokClient


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one conversation entity per conversation subentry, or legacy models."""
    specs = conversation_agent_specs(config_entry)
    legacy: list[conversation.ConversationEntity] = []
    for subentry, model, default_agent in specs:
        entity = GrokConversationEntity(
            config_entry,
            model,
            subentry=subentry,
            default_agent=default_agent,
        )
        if subentry is not None:
            async_add_entities([entity], config_subentry_id=subentry.subentry_id)
        else:
            legacy.append(entity)
    if legacy:
        LOGGER.debug("Setting up %s legacy Grok conversation entities", len(legacy))
        async_add_entities(legacy)

    if REALTIME_ENABLED and has_realtime(
        config_entry.data.get(CONF_SELECTED_MODELS, [])
    ):
        async_add_entities(
            [
                GrokConversationEntity(
                    config_entry,
                    "grok-voice-latest",
                    realtime=True,
                    default_agent=not specs,
                )
            ]
        )


class GrokConversationEntity(
    conversation.ConversationEntity,
    conversation.AbstractConversationAgent,
):
    """Grok conversation agent backed by SuperGrok OAuth."""

    _attr_has_entity_name = True
    _attr_supports_streaming = True

    def __init__(
        self,
        entry: ConfigEntry,
        model: str | None = None,
        *,
        subentry: ConfigSubentry | None = None,
        realtime: bool = False,
        default_agent: bool = False,
    ) -> None:
        self.entry = entry
        self.subentry = subentry
        self._realtime = realtime
        self._default_agent = default_agent
        if subentry is not None:
            self._model = str(
                config_option(entry, subentry, CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)
            )
            self._attr_unique_id = subentry.subentry_id
            self._attr_name = None
            device_name = subentry.title
        else:
            self._model = model or DEFAULT_CHAT_MODEL
            self._attr_unique_id = f"{entry.entry_id}_{'realtime' if realtime else self._model}"
            catalog = CATALOG_BY_ID.get(self._model)
            device_name = (
                DEFAULT_REALTIME_NAME
                if realtime
                else (catalog.label if catalog else f"Grok {self._model}")
            )
            self._attr_name = None
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=device_name,
            manufacturer="xAI",
            model=self._model,
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        if self._llm_hass_api():
            self._attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def _llm_hass_api(self) -> list[str] | str | None:
        """Control Home Assistant APIs from the subentry, else the parent entry."""
        return config_option(self.entry, self.subentry, CONF_LLM_HASS_API)

    def _prompt(self) -> str | None:
        """System prompt from the subentry, else the parent entry."""
        return config_option(self.entry, self.subentry, CONF_PROMPT)

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
                self._llm_hass_api(),
                self._prompt(),
                user_input.extra_system_prompt,
            )
        except conversation.ConverseError as err:
            return err.as_conversation_result()

        client: GrokClient = self.entry.runtime_data
        model = config_option(self.entry, self.subentry, CONF_CHAT_MODEL, self._model)
        max_tokens = int(
            config_option(self.entry, self.subentry, CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)
            or DEFAULT_MAX_TOKENS
        )
        temperature = config_option(self.entry, self.subentry, CONF_TEMPERATURE)
        LOGGER.debug(
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
                max_tokens=max_tokens,
                temperature=float(temperature) if temperature is not None else None,
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
