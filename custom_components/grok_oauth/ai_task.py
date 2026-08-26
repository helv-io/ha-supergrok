"""AI Task entity: generate_data plus Imagine /v1/images/generations."""

from __future__ import annotations

from json import JSONDecodeError
from typing import TYPE_CHECKING

from homeassistant.components import ai_task, conversation
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_CHAT_MODEL,
    CONF_IMAGE_MODEL,
    CONF_MAX_TOKENS,
    CONF_SELECTED_MODELS,
    CONF_TEMPERATURE,
    DEFAULT_AI_TASK_NAME,
    DEFAULT_CHAT_MODEL,
    DEFAULT_MAX_TOKENS,
    DOMAIN,
    LOGGER,
    SUBENTRY_TYPE_AI_TASK,
)
from .helpers import async_run_chat_log
from .jsonutil import extract_json_object
from .models import chat_models, config_option, first_image_model

if TYPE_CHECKING:
    from .client import GrokClient


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Grok AI Task entities from subentries, or one legacy entity."""
    ai_task_subentries = config_entry.get_subentries_of_type(SUBENTRY_TYPE_AI_TASK)
    if ai_task_subentries:
        for subentry in ai_task_subentries:
            async_add_entities(
                [GrokAITaskEntity(config_entry, subentry=subentry)],
                config_subentry_id=subentry.subentry_id,
            )
        return
    async_add_entities([GrokAITaskEntity(config_entry)])


class GrokAITaskEntity(ai_task.AITaskEntity):
    """AI Task backed by SuperGrok chat + Imagine."""

    _attr_has_entity_name = True
    _attr_name = DEFAULT_AI_TASK_NAME

    def __init__(
        self, entry: ConfigEntry, *, subentry: ConfigSubentry | None = None
    ) -> None:
        self.entry = entry
        self.subentry = subentry
        selected = entry.data.get(CONF_SELECTED_MODELS, [DEFAULT_CHAT_MODEL])
        chats = chat_models(selected)
        default_chat = entry.data.get(CONF_CHAT_MODEL) or (
            chats[0] if chats else DEFAULT_CHAT_MODEL
        )
        self._chat_model = config_option(entry, subentry, CONF_CHAT_MODEL, default_chat)
        self._image_model = config_option(
            entry, subentry, CONF_IMAGE_MODEL, first_image_model(selected)
        )
        if subentry is not None:
            self._attr_unique_id = subentry.subentry_id
            self._attr_name = None
            device_name = subentry.title or DEFAULT_AI_TASK_NAME
        else:
            self._attr_unique_id = f"{entry.entry_id}_ai_task"
            device_name = DEFAULT_AI_TASK_NAME
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=device_name,
            manufacturer="xAI",
            model=self._chat_model,
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        features = (
            ai_task.AITaskEntityFeature.GENERATE_DATA
            | ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
        )
        if self._image_model:
            features |= ai_task.AITaskEntityFeature.GENERATE_IMAGE
        self._attr_supported_features = features

    async def _async_generate_data(
        self,
        task: ai_task.GenDataTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenDataTaskResult:
        """Handle generate_data via chat completions."""
        client: GrokClient = self.entry.runtime_data
        max_tokens = int(
            config_option(self.entry, self.subentry, CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)
            or DEFAULT_MAX_TOKENS
        )
        temperature = config_option(self.entry, self.subentry, CONF_TEMPERATURE)
        await async_run_chat_log(
            client=client,
            chat_log=chat_log,
            model=self._chat_model,
            agent_id=self.entity_id,
            max_tokens=max_tokens,
            temperature=float(temperature) if temperature is not None else None,
            response_format={"type": "json_object"} if task.structure else None,
        )
        if not isinstance(chat_log.content[-1], conversation.AssistantContent):
            raise HomeAssistantError("Grok did not return an assistant message")
        text = chat_log.content[-1].content or ""
        if not task.structure:
            return ai_task.GenDataTaskResult(
                conversation_id=chat_log.conversation_id,
                data=text,
            )
        try:
            data = extract_json_object(text)
        except JSONDecodeError as err:
            LOGGER.error("Structured Grok response was not JSON: %s", text[:300])
            raise HomeAssistantError("Grok structured response was not valid JSON") from err
        return ai_task.GenDataTaskResult(
            conversation_id=chat_log.conversation_id,
            data=data,
        )

    async def _async_generate_image(
        self,
        task: ai_task.GenImageTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenImageTaskResult:
        """Handle generate_image via /v1/images/generations (and /v1/image/generations)."""
        if not self._image_model:
            raise HomeAssistantError("No Imagine model is enabled on this SuperGrok OAuth entry")
        prompt = task.instructions or ""
        if not prompt:
            for content in reversed(chat_log.content):
                if getattr(content, "role", None) == "user" and content.content:
                    prompt = content.content
                    break
        if not prompt:
            raise HomeAssistantError("No prompt provided for Grok Imagine")

        client: GrokClient = self.entry.runtime_data
        images = await client.generate_image(
            prompt=prompt,
            model=self._image_model,
            n=1,
            response_format="b64_json",
        )
        image = images[0]
        image_data = image.image_bytes()
        if image_data is None and image.url:
            session = client.session
            async with session.get(image.url) as resp:
                if resp.status >= 400:
                    raise HomeAssistantError(f"Could not download Imagine image ({resp.status})")
                image_data = await resp.read()
                mime = resp.headers.get("Content-Type", image.mime_type)
        else:
            mime = image.mime_type
        if not image_data:
            raise HomeAssistantError("Grok Imagine returned no image bytes")

        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=self.entity_id,
                content=image.revised_prompt or prompt,
            )
        )
        return ai_task.GenImageTaskResult(
            image_data=image_data,
            conversation_id=chat_log.conversation_id,
            mime_type=mime or "image/jpeg",
            model=self._image_model,
            revised_prompt=image.revised_prompt,
        )
