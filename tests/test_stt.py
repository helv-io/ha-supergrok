"""STT entity forwards Assist language to the xAI client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.components import stt as ha_stt

from custom_components.supergrok.stt import GrokSTTEntity


def _metadata(language: str) -> ha_stt.SpeechMetadata:
    return ha_stt.SpeechMetadata(
        language=language,
        format=ha_stt.AudioFormats.WAV,
        codec=ha_stt.AudioCodecs.PCM,
        bit_rate=ha_stt.AudioBitRates.BITRATE_16,
        sample_rate=ha_stt.AudioSampleRates.SAMPLERATE_16000,
        channel=ha_stt.AudioChannels.CHANNEL_MONO,
    )


async def test_entity_forwards_assist_language() -> None:
    """HA SpeechMetadata.language is passed through, not dropped."""
    client = MagicMock()
    client.stt = AsyncMock(return_value="acender as luzes")
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.runtime_data = client
    entity = GrokSTTEntity(entry)

    async def _stream():
        yield b"RIFF....pcm"

    result = await entity.async_process_audio_stream(_metadata("pt-BR"), _stream())
    assert result.text == "acender as luzes"
    assert client.stt.await_args.kwargs["language"] == "pt-BR"
