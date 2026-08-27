"""HTTP client host fallback, 429, TTS audio, STT hosts. No live xAI."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.supergrok.client import GrokClient
from custom_components.supergrok.const import (
    CLIENT_IDENTIFIER,
    MEDIA_API_BASES,
    STT_FORMAT_LANGUAGES,
    STT_HA_LANGUAGES,
    TTS_LANGUAGE_MAP,
    stt_format_language,
)
from custom_components.supergrok.oauth import OAuthTokens

TOKENS = OAuthTokens(
    access_token="access",
    refresh_token="refresh",
    expires_at=9999999999,
)


class FakeResp:
    """Minimal aiohttp response."""

    def __init__(self, status: int, body, headers: dict | None = None) -> None:
        self.status = status
        if isinstance(body, (bytes, bytearray)):
            self._body = bytes(body)
        elif isinstance(body, str):
            self._body = body.encode()
        else:
            self._body = json.dumps(body).encode()
        self.headers = headers or {"Content-Type": "application/json"}

    async def read(self) -> bytes:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    """Queue of FakeResp values for request/post."""

    def __init__(self, responses: list[FakeResp]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected {method} {url}")
        return self.responses.pop(0)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)


def _client():
    return GrokClient(MagicMock(), TOKENS)


async def test_chat_falls_back_from_402_to_second_host() -> None:
    """402 on grok.com continues to api.x.ai."""
    session = FakeSession(
        [
            FakeResp(402, {"error": "payment"}),
            FakeResp(
                200,
                {
                    "choices": [
                        {
                            "message": {"content": "hello"},
                            "finish_reason": "stop",
                        }
                    ]
                },
            ),
        ]
    )
    client = _client()
    with patch(
        "custom_components.supergrok.client.async_get_clientsession",
        return_value=session,
    ):
        result = await client.chat(model="grok-4.6", messages=[{"role": "user", "content": "hi"}])
    assert result.text == "hello"
    assert "cli-chat-proxy.grok.com" in session.calls[0][1]
    assert "api.x.ai" in session.calls[1][1]


async def test_429_retries_then_succeeds() -> None:
    """429 sleeps and retries the same host."""
    session = FakeSession(
        [
            FakeResp(429, {"error": "rate"}, headers={"Retry-After": "0", "Content-Type": "application/json"}),
            FakeResp(
                200,
                {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
            ),
        ]
    )
    client = _client()
    with (
        patch(
            "custom_components.supergrok.client.async_get_clientsession",
            return_value=session,
        ),
        patch("custom_components.supergrok.client.asyncio.sleep", new=AsyncMock()),
    ):
        result = await client.chat(model="grok-4.6", messages=[{"role": "user", "content": "hi"}])
    assert result.text == "ok"
    assert len(session.calls) == 2
    assert session.calls[0][1] == session.calls[1][1]


async def test_tts_raw_audio_is_one_post() -> None:
    """audio/mpeg is returned on the first walk; no second POST."""
    audio = b"ID3fake-mp3"
    session = FakeSession(
        [FakeResp(200, audio, headers={"Content-Type": "audio/mpeg"})]
    )
    client = _client()
    with patch(
        "custom_components.supergrok.client.async_get_clientsession",
        return_value=session,
    ):
        body, content_type = await client.tts(text="hello", voice_id="eve")
    assert body == audio
    assert content_type == "audio/mpeg"
    assert len(session.calls) == 1
    assert "language" not in (session.calls[0][2].get("json") or {})


def _form_text_fields(form) -> dict[str, str]:
    """Pull text fields from aiohttp.FormData (version-tolerant)."""
    fields: dict[str, str] = {}
    for item in getattr(form, "_fields", ()):
        name = None
        value = None
        if isinstance(item, tuple) and item:
            first = item[0]
            if isinstance(first, str):
                name = first
                value = item[1] if len(item) > 1 else None
            elif hasattr(first, "get"):
                name = first.get("name")
                value = item[2] if len(item) > 2 else item[1] if len(item) > 1 else None
        if not name or isinstance(value, (bytes, bytearray)):
            continue
        if isinstance(value, str):
            fields[name] = value
    return fields


def _assert_stt_format_language(form, expected_language: str | None) -> None:
    """format=true is only legal together with an xAI short language code."""
    fields = _form_text_fields(form)
    if fields.get("format") == "true":
        assert fields.get("language") == expected_language
        assert expected_language in STT_FORMAT_LANGUAGES
        assert "-" not in expected_language
    else:
        assert "language" not in fields


def test_stt_format_language_maps_bcp47_to_short_code() -> None:
    """HA Assist tags become ISO 639-1 codes, never TTS_LANGUAGE_MAP values."""
    assert stt_format_language("en-US") == "en"
    assert stt_format_language("en-GB") == "en"
    assert stt_format_language("pt-BR") == "pt"
    assert stt_format_language("pt-PT") == "pt"
    assert stt_format_language("de-DE") == "de"
    assert stt_format_language("es-MX") == "es"
    assert stt_format_language("es-ES") == "es"
    assert stt_format_language("ja-JP") == "ja"
    assert stt_format_language("fil") == "fil"
    assert stt_format_language("fil-PH") == "fil"
    assert stt_format_language("pt-BR") != TTS_LANGUAGE_MAP["pt-BR"]
    assert stt_format_language("es-ES") != TTS_LANGUAGE_MAP["es-ES"]
    assert stt_format_language("zh-CN") is None
    assert stt_format_language("bn") is None
    assert stt_format_language(None) is None
    assert stt_format_language("") is None
    assert stt_format_language("  ") is None
    for tag in STT_HA_LANGUAGES:
        mapped = stt_format_language(tag)
        if mapped is not None:
            assert mapped in STT_FORMAT_LANGUAGES
            assert "-" not in mapped
    for tag in STT_HA_LANGUAGES:
        mapped = stt_format_language(tag)
        if mapped is not None:
            assert mapped in STT_FORMAT_LANGUAGES
            assert "-" not in mapped


async def test_stt_prefers_grok_com() -> None:
    """STT hits grok.com first and sends language whenever format is true."""
    session = FakeSession([FakeResp(200, {"text": "lights on"})])
    client = _client()
    with patch(
        "custom_components.supergrok.client.async_get_clientsession",
        return_value=session,
    ):
        text = await client.stt(
            audio=b"RIFF....",
            filename="speech.wav",
            content_type="audio/wav",
            language="en-US",
        )
    assert text == "lights on"
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url.startswith(MEDIA_API_BASES[0])
    assert "/stt" in url
    form = kwargs.get("data")
    assert form is not None
    _assert_stt_format_language(form, "en")


async def test_stt_falls_back_to_api_xai() -> None:
    """402 on grok.com STT continues to api.x.ai."""
    session = FakeSession(
        [
            FakeResp(402, {"error": "payment"}),
            FakeResp(200, {"text": "ok"}),
        ]
    )
    client = _client()
    with patch(
        "custom_components.supergrok.client.async_get_clientsession",
        return_value=session,
    ):
        text = await client.stt(
            audio=b"RIFF",
            filename="speech.wav",
            content_type="audio/wav",
            language="de-DE",
        )
    assert text == "ok"
    assert "cli-chat-proxy.grok.com" in session.calls[0][1]
    assert "api.x.ai" in session.calls[1][1]
    for _method, _url, kwargs in session.calls:
        _assert_stt_format_language(kwargs.get("data"), "de")


async def test_stt_maps_bcp47_on_pcm_and_container() -> None:
    """PCM and WAV/OGG attempts both send the short code with format=true."""
    session = FakeSession(
        [
            FakeResp(400, {"error": "Field 'language' is required when 'format' is true"}),
            FakeResp(200, {"text": "luzes"}),
        ]
    )
    client = _client()
    with patch(
        "custom_components.supergrok.client.async_get_clientsession",
        return_value=session,
    ):
        text = await client.stt(
            audio=b"RIFF....",
            filename="speech.wav",
            content_type="audio/wav",
            sample_rate=16000,
            raw_pcm=b"\x00\x00",
            language="pt-BR",
        )
    assert text == "luzes"
    assert len(session.calls) == 2
    for _method, _url, kwargs in session.calls:
        fields = _form_text_fields(kwargs.get("data"))
        assert fields.get("format") == "true"
        assert fields.get("language") == "pt"
        _assert_stt_format_language(kwargs.get("data"), "pt")


async def test_stt_omits_format_when_language_unsupported() -> None:
    """Languages xAI cannot format must not post format=true (that 400s)."""
    session = FakeSession([FakeResp(200, {"text": "ok"})])
    client = _client()
    with patch(
        "custom_components.supergrok.client.async_get_clientsession",
        return_value=session,
    ):
        text = await client.stt(
            audio=b"RIFF",
            filename="speech.wav",
            content_type="audio/wav",
            language="zh-CN",
        )
    assert text == "ok"
    fields = _form_text_fields(session.calls[0][2].get("data"))
    assert fields.get("format") != "true"
    assert "language" not in fields


async def test_stt_omits_format_when_language_missing() -> None:
    """No HA language means no format=true, so the request cannot 400."""
    session = FakeSession([FakeResp(200, {"text": "ok"})])
    client = _client()
    with patch(
        "custom_components.supergrok.client.async_get_clientsession",
        return_value=session,
    ):
        text = await client.stt(
            audio=b"RIFF",
            filename="speech.wav",
            content_type="audio/wav",
        )
    assert text == "ok"
    _assert_stt_format_language(session.calls[0][2].get("data"), None)


async def test_headers_identify_as_ha_supergrok() -> None:
    """grok.com requests send ha-supergrok, not grok-shell, on the first try."""
    session = FakeSession(
        [
            FakeResp(
                200,
                {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]},
            )
        ]
    )
    client = _client()
    with patch(
        "custom_components.supergrok.client.async_get_clientsession",
        return_value=session,
    ):
        await client.chat(model="grok-4.6", messages=[{"role": "user", "content": "hi"}])
    headers = session.calls[0][2]["headers"]
    assert headers["x-grok-client-identifier"] == CLIENT_IDENTIFIER
    assert headers["x-xai-token-auth"] == "xai-grok-cli"


async def test_chat_stream_yields_content_then_finish() -> None:
    """SSE chunks become content deltas plus a finish event."""

    class StreamResp(FakeResp):
        def __init__(self) -> None:
            super().__init__(200, b"")
            chunks = [
                b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n',
                b'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}]}\n',
                b"data: [DONE]\n",
            ]

            async def _iter():
                for chunk in chunks:
                    yield chunk

            self.content = _iter()

    session = FakeSession([StreamResp()])
    client = _client()
    events = []
    with patch(
        "custom_components.supergrok.client.async_get_clientsession",
        return_value=session,
    ):
        async for event in client.chat_stream(
            model="grok-4.6", messages=[{"role": "user", "content": "hi"}]
        ):
            events.append(event)
    assert events[0]["content"] == "Hel"
    assert events[1]["content"] == "lo"
    assert events[-1]["finish_reason"] == "stop"
    assert events[-1]["tool_calls"] == []


async def test_empty_stt_raises() -> None:
    """Empty transcript is an error, not a hang."""
    session = FakeSession([FakeResp(200, {"duration": 1.2})])
    client = _client()
    with patch(
        "custom_components.supergrok.client.async_get_clientsession",
        return_value=session,
    ):
        with pytest.raises(HomeAssistantError, match="no text"):
            await client.stt(
                audio=b"RIFF",
                filename="speech.wav",
                content_type="audio/wav",
                language="en-US",
            )
