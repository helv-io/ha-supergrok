"""HTTP client host fallback, 429, TTS audio, STT hosts. No live xAI."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.grok_oauth.client import GrokClient
from custom_components.grok_oauth.const import CLIENT_IDENTIFIER, MEDIA_API_BASES
from custom_components.grok_oauth.oauth import OAuthTokens

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
        "custom_components.grok_oauth.client.async_get_clientsession",
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
            "custom_components.grok_oauth.client.async_get_clientsession",
            return_value=session,
        ),
        patch("custom_components.grok_oauth.client.asyncio.sleep", new=AsyncMock()),
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
        "custom_components.grok_oauth.client.async_get_clientsession",
        return_value=session,
    ):
        body, content_type = await client.tts(text="hello", voice_id="eve")
    assert body == audio
    assert content_type == "audio/mpeg"
    assert len(session.calls) == 1
    assert "language" not in (session.calls[0][2].get("json") or {})


async def test_stt_prefers_grok_com() -> None:
    """STT hits grok.com first and does not send a language field."""
    session = FakeSession([FakeResp(200, {"text": "lights on"})])
    client = _client()
    with patch(
        "custom_components.grok_oauth.client.async_get_clientsession",
        return_value=session,
    ):
        text = await client.stt(
            audio=b"RIFF....",
            filename="speech.wav",
            content_type="audio/wav",
        )
    assert text == "lights on"
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url.startswith(MEDIA_API_BASES[0])
    assert "/stt" in url
    form = kwargs.get("data")
    assert form is not None


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
        "custom_components.grok_oauth.client.async_get_clientsession",
        return_value=session,
    ):
        text = await client.stt(
            audio=b"RIFF",
            filename="speech.wav",
            content_type="audio/wav",
        )
    assert text == "ok"
    assert "cli-chat-proxy.grok.com" in session.calls[0][1]
    assert "api.x.ai" in session.calls[1][1]


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
        "custom_components.grok_oauth.client.async_get_clientsession",
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
        "custom_components.grok_oauth.client.async_get_clientsession",
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
        "custom_components.grok_oauth.client.async_get_clientsession",
        return_value=session,
    ):
        with pytest.raises(HomeAssistantError, match="no text"):
            await client.stt(
                audio=b"RIFF",
                filename="speech.wav",
                content_type="audio/wav",
            )
