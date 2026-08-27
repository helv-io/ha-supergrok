"""Text-to-speech via POST /v1/tts: the HA surface that accepts Grok Voice TTS."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.components.tts import (
    ATTR_PREFERRED_FORMAT,
    ATTR_VOICE,
    TextToSpeechEntity,
    TtsAudioType,
    Voice,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_SELECTED_MODELS,
    CONF_TTS_VOICE,
    DEFAULT_TTS_NAME,
    DEFAULT_TTS_VOICE,
    DOMAIN,
    FALLBACK_TTS_VOICES,
    LOGGER,
    TTS_HA_LANGUAGES,
)
from .models import has_voice

if TYPE_CHECKING:
    from .client import GrokClient


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Expose Grok Voice TTS when Voice is selected."""
    selected = config_entry.data.get(CONF_SELECTED_MODELS, [])
    if not has_voice(selected):
        return
    async_add_entities([GrokTTSEntity(config_entry)])


class GrokTTSEntity(TextToSpeechEntity):
    """Home Assistant TTS entity wrapping POST /v1/tts."""

    # TTS manager requires engine.name; None raises "TTS engine name is not set."
    _attr_has_entity_name = False
    _attr_name = DEFAULT_TTS_NAME
    _attr_supported_options = [ATTR_VOICE, ATTR_PREFERRED_FORMAT]
    _attr_supported_languages = TTS_HA_LANGUAGES
    _attr_default_language = "en-US"

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_tts"
        self._voices = [Voice(voice_id, name) for voice_id, name in FALLBACK_TTS_VOICES]
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=DEFAULT_TTS_NAME,
            manufacturer="xAI",
            model="grok-voice-tts",
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Refresh the live voice list from GET /v1/tts/voices."""
        await super().async_added_to_hass()
        client: GrokClient = self.entry.runtime_data
        try:
            remote = await client.list_tts_voices()
        except Exception:  # noqa: BLE001 - catalog is optional
            LOGGER.debug("Using built-in Grok voice list")
            return
        if remote:
            self._voices = [Voice(voice_id, name) for voice_id, name in remote]
            LOGGER.debug("TTS loaded %s Grok voices", len(self._voices))

    @callback
    def async_get_supported_voices(self, language: str) -> list[Voice]:
        """Return Grok voices. language is forwarded on POST /v1/tts."""
        return self._voices

    @property
    def default_options(self) -> Mapping[str, Any]:
        """Default to Eve / MP3, both first-class on POST /v1/tts."""
        default_voice = self.entry.data.get(CONF_TTS_VOICE, DEFAULT_TTS_VOICE)
        return {ATTR_VOICE: default_voice, ATTR_PREFERRED_FORMAT: "mp3"}

    async def async_get_tts_audio(
        self, message: str, language: str, options: dict[str, Any]
    ) -> TtsAudioType:
        """Synthesize speech. HA accepts mp3 / wav / pcm from this entity."""
        preferred = (options.get(ATTR_PREFERRED_FORMAT) or "mp3").lower()
        codec = "wav" if preferred in ("wav", "wave") else "pcm" if preferred in ("pcm", "raw") else "mp3"
        extension = "wav" if codec == "wav" else "mp3" if codec == "mp3" else "raw"
        LOGGER.debug(
            "TTS request voice=%s ha_lang=%s codec=%s chars=%s",
            options.get(ATTR_VOICE, DEFAULT_TTS_VOICE),
            language,
            codec,
            len(message),
        )
        client: GrokClient = self.entry.runtime_data
        try:
            audio, _content_type = await client.tts(
                text=message,
                voice_id=options.get(ATTR_VOICE, DEFAULT_TTS_VOICE),
                codec=codec,
                language=language,
            )
        except Exception as err:
            LOGGER.exception("Grok TTS failed")
            raise HomeAssistantError(f"Grok TTS failed: {err}") from err
        return extension, audio
