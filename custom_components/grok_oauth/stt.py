"""Speech-to-text via POST /v1/stt: the HA surface that accepts Grok Voice STT."""

from __future__ import annotations

import io
import wave
from collections.abc import AsyncIterable
from typing import TYPE_CHECKING

from homeassistant.components import stt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_SELECTED_MODELS,
    DEFAULT_STT_NAME,
    DOMAIN,
    LOGGER,
    STT_HA_LANGUAGES,
)
from .models import has_voice

if TYPE_CHECKING:
    from .client import GrokClient


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Expose Grok Voice STT when Voice is selected."""
    selected = config_entry.data.get(CONF_SELECTED_MODELS, [])
    if not has_voice(selected):
        return
    async_add_entities([GrokSTTEntity(config_entry)])


class GrokSTTEntity(stt.SpeechToTextEntity):
    """Home Assistant STT entity wrapping POST /v1/stt."""

    _attr_has_entity_name = False
    _attr_name = DEFAULT_STT_NAME

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_stt"
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=DEFAULT_STT_NAME,
            manufacturer="xAI",
            model="grok-stt",
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    @property
    def supported_languages(self) -> list[str]:
        """Languages Home Assistant may send; xAI STT accepts the short codes."""
        return list(STT_HA_LANGUAGES)

    @property
    def supported_formats(self) -> list[stt.AudioFormats]:
        """HA Voice satellites emit WAV/PCM or OGG/Opus, both are valid on /v1/stt."""
        return [stt.AudioFormats.WAV, stt.AudioFormats.OGG]

    @property
    def supported_codecs(self) -> list[stt.AudioCodecs]:
        """PCM (WAV) and Opus (OGG) are the HA Assist codecs."""
        return [stt.AudioCodecs.PCM, stt.AudioCodecs.OPUS]

    @property
    def supported_bit_rates(self) -> list[stt.AudioBitRates]:
        """Bit depths HA satellites use."""
        return [
            stt.AudioBitRates.BITRATE_8,
            stt.AudioBitRates.BITRATE_16,
            stt.AudioBitRates.BITRATE_24,
            stt.AudioBitRates.BITRATE_32,
        ]

    @property
    def supported_sample_rates(self) -> list[stt.AudioSampleRates]:
        """Sample rates that overlap HA Assist and xAI STT (8k–48k)."""
        rates = [
            stt.AudioSampleRates.SAMPLERATE_8000,
            stt.AudioSampleRates.SAMPLERATE_16000,
            stt.AudioSampleRates.SAMPLERATE_22000,
            stt.AudioSampleRates.SAMPLERATE_44100,
            stt.AudioSampleRates.SAMPLERATE_48000,
        ]
        extra = getattr(stt.AudioSampleRates, "SAMPLERATE_24000", None)
        if extra is not None and extra not in rates:
            rates.insert(3, extra)
        return rates

    @property
    def supported_channels(self) -> list[stt.AudioChannels]:
        """Mono is what Assist sends; stereo is accepted by xAI."""
        return [stt.AudioChannels.CHANNEL_MONO, stt.AudioChannels.CHANNEL_STEREO]

    async def async_process_audio_stream(
        self, metadata: stt.SpeechMetadata, stream: AsyncIterable[bytes]
    ) -> stt.SpeechResult:
        """Buffer the HA audio stream and POST it to /v1/stt."""
        audio_bytes = bytearray()
        async for chunk in stream:
            audio_bytes.extend(chunk)
        audio_data = bytes(audio_bytes)
        raw_pcm: bytes | None = None
        sample_rate = int(metadata.sample_rate.value)
        LOGGER.debug(
            "STT stream format=%s codec=%s rate=%s bit=%s ch=%s bytes=%s lang=%s riff=%s",
            metadata.format,
            metadata.codec,
            sample_rate,
            metadata.bit_rate,
            metadata.channel,
            len(audio_data),
            metadata.language,
            audio_data[:4] == b"RIFF",
        )

        if metadata.format == stt.AudioFormats.WAV:
            if audio_data[:4] == b"RIFF":
                container = audio_data
            else:
                raw_pcm = audio_data
                buffer = io.BytesIO()
                with wave.open(buffer, "wb") as wav_file:
                    wav_file.setnchannels(metadata.channel.value)
                    wav_file.setsampwidth(max(metadata.bit_rate.value // 8, 1))
                    wav_file.setframerate(sample_rate)
                    wav_file.writeframes(audio_data)
                container = buffer.getvalue()
            filename = "speech.wav"
            content_type = "audio/wav"
        else:
            container = audio_data
            filename = "speech.ogg"
            content_type = "audio/ogg"

        # 22000 Hz is an HA enum; xAI STT wants 22050 for raw PCM.
        pcm_rate = 22050 if sample_rate == 22000 else sample_rate

        client: GrokClient = self.entry.runtime_data
        try:
            text = await client.stt(
                audio=container,
                filename=filename,
                content_type=content_type,
                sample_rate=pcm_rate,
                raw_pcm=raw_pcm,
                channels=int(metadata.channel.value),
            )
        except Exception:
            LOGGER.exception("Grok STT failed")
            return stt.SpeechResult(None, stt.SpeechResultState.ERROR)
        if not text:
            return stt.SpeechResult(None, stt.SpeechResultState.ERROR)
        return stt.SpeechResult(text, stt.SpeechResultState.SUCCESS)
