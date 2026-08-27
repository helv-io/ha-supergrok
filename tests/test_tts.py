"""TTS entity forwards Assist language to the xAI client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from custom_components.supergrok.tts import GrokTTSEntity


async def test_entity_forwards_assist_language() -> None:
    """HA async_get_tts_audio language is passed through, not dropped."""
    client = MagicMock()
    client.tts = AsyncMock(return_value=(b"ID3fake", "audio/mpeg"))
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.runtime_data = client
    entity = GrokTTSEntity(entry)

    extension, audio = await entity.async_get_tts_audio(
        "Glad you like it.", "en-US", {"voice": "eve"}
    )
    assert extension == "mp3"
    assert audio == b"ID3fake"
    assert client.tts.await_args.kwargs["language"] == "en-US"
    assert client.tts.await_args.kwargs["text"] == "Glad you like it."
    assert client.tts.await_args.kwargs["voice_id"] == "eve"
