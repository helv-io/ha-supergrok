"""Authenticated xAI / Grok CLI client used by the HA platforms."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CHAT_API_BASES,
    CLIENT_IDENTIFIER,
    CLIENT_IDENTIFIER_FALLBACK,
    CLIENT_VERSION_FALLBACK,
    GROK_CLI_TOKEN_AUTH,
    IMAGE_PATHS,
    LOGGER,
    MAX_STATUS_RETRIES,
    MEDIA_API_BASES,
    REALTIME_WS_URLS,
    RETRY_STATUS,
)
from .logutil import elapsed_ms, preview, summarize_tools
from .oauth import GrokOAuthError, OAuthTokens, refresh_tokens

PersistTokens = Callable[[OAuthTokens], Awaitable[None] | None]


def _integration_version() -> str:
    """Read the integration version from manifest.json."""
    try:
        payload = json.loads(
            (Path(__file__).resolve().parent / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return CLIENT_VERSION_FALLBACK
    version = payload.get("version")
    return str(version) if version else CLIENT_VERSION_FALLBACK


@dataclass(slots=True)
class ChatResult:
    """A single chat completion turn."""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ImageResult:
    """One generated image."""

    url: str | None
    b64_json: str | None
    revised_prompt: str | None
    mime_type: str
    model: str

    def image_bytes(self) -> bytes | None:
        """Decode base64 image data if present."""
        if not self.b64_json:
            return None
        return base64.b64decode(self.b64_json)


class GrokClient:
    """Refresh-aware HTTP client for SuperGrok OAuth."""

    def __init__(
        self,
        hass: HomeAssistant,
        tokens: OAuthTokens,
        persist: PersistTokens | None = None,
    ) -> None:
        self.hass = hass
        self._tokens = tokens
        self._persist = persist
        self._refresh_lock = asyncio.Lock()
        self._chat_base = CHAT_API_BASES[0]
        self._media_base = MEDIA_API_BASES[0]
        self._client_identifier = CLIENT_IDENTIFIER
        self._client_version = _integration_version()
        self._models_cache: list[str] | None = None
        self.recent_events: deque[str] = deque(maxlen=20)

    def _note(self, message: str) -> None:
        """Keep a short ring buffer for diagnostics."""
        self.recent_events.append(message)

    @property
    def session(self) -> aiohttp.ClientSession:
        """Shared HA client session."""
        return async_get_clientsession(self.hass)

    @property
    def tokens(self) -> OAuthTokens:
        """Current token pair."""
        return self._tokens

    def _remember_base(self, base: str) -> None:
        """Stick to the host that last succeeded for this traffic class."""
        if base in CHAT_API_BASES:
            self._chat_base = base
        if base in MEDIA_API_BASES:
            self._media_base = base

    async def async_access_token(self) -> str:
        """Return a non-expired access token, refreshing if needed."""
        if not self._tokens.is_expired():
            return self._tokens.access_token
        async with self._refresh_lock:
            if not self._tokens.is_expired():
                return self._tokens.access_token
            LOGGER.debug("Access token expired; refreshing SuperGrok session")
            try:
                refreshed = await refresh_tokens(self.session, self._tokens)
            except GrokOAuthError as err:
                LOGGER.warning(
                    "SuperGrok token refresh failed (%s): %s",
                    err.reason,
                    preview(err.details),
                )
                self._note(f"refresh_failed:{err.reason}")
                if err.reason in ("reauth_required", "tier_blocked"):
                    raise ConfigEntryAuthFailed(err.details or err.reason) from err
                raise HomeAssistantError(
                    f"SuperGrok OAuth refresh failed: {err.details or err.reason}"
                ) from err
            self._tokens = refreshed
            if self._persist:
                result = self._persist(refreshed)
                if asyncio.iscoroutine(result):
                    await result
            LOGGER.info("Refreshed SuperGrok access token (expires_at=%s)", refreshed.expires_at)
            self._note(f"refresh_ok:expires_at={refreshed.expires_at}")
            return refreshed.access_token

    def _headers(
        self,
        *,
        json_body: bool = True,
        extra: Mapping[str, str] | None = None,
        base: str | None = None,
        identifier: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._tokens.access_token}",
            "Accept": "application/json",
        }
        if base and "grok.com" in base:
            headers["x-xai-token-auth"] = GROK_CLI_TOKEN_AUTH
            headers["x-grok-client-identifier"] = identifier or self._client_identifier
            headers["x-grok-client-version"] = self._client_version
        if json_body:
            headers["Content-Type"] = "application/json"
        if extra:
            headers.update(extra)
        return headers

    def _ordered_bases(
        self, bases: tuple[str, ...], preferred_base: str | None
    ) -> list[str]:
        ordered: list[str] = []
        if preferred_base:
            ordered.append(preferred_base)
        for base in bases:
            if base not in ordered:
                ordered.append(base)
        return ordered

    async def _request(
        self,
        method: str,
        bases: tuple[str, ...],
        path: str,
        *,
        preferred_base: str | None = None,
        json_data: dict[str, Any] | None = None,
        data: aiohttp.FormData | None = None,
        params: dict[str, Any] | None = None,
        timeout: int = 120,
        raw: bool = False,
        binary_ok: bool = False,
        extra_paths: tuple[str, ...] = (),
    ) -> Any:
        """Issue an authenticated request, trying hosts / path aliases."""
        await self.async_access_token()
        ordered_bases = self._ordered_bases(bases, preferred_base)
        paths = (path, *extra_paths)
        last_error: Exception | None = None
        last_status = 0
        last_body = ""
        identifier = self._client_identifier

        for base in ordered_bases:
            for candidate in paths:
                url = f"{base.rstrip('/')}{candidate}"
                tried_shell_fallback = False
                for attempt in range(MAX_STATUS_RETRIES + 1):
                    started = time.monotonic()
                    try:
                        async with self.session.request(
                            method,
                            url,
                            headers=self._headers(
                                json_body=json_data is not None,
                                base=base,
                                identifier=identifier,
                            ),
                            json=json_data,
                            data=data,
                            params=params,
                            timeout=aiohttp.ClientTimeout(total=timeout),
                        ) as resp:
                            body = await resp.read()
                            content_type = resp.headers.get(
                                "Content-Type", "application/octet-stream"
                            )
                            if (
                                resp.status in (401, 403)
                                and base == ordered_bases[0]
                                and candidate == paths[0]
                                and attempt == 0
                            ):
                                LOGGER.info(
                                    "Grok %s %s -> %s; refreshing token and retrying",
                                    method,
                                    url,
                                    resp.status,
                                )
                                self._tokens.expires_at = 0
                                await self.async_access_token()
                                continue
                            if (
                                resp.status in (401, 403)
                                and base
                                and "grok.com" in base
                                and identifier == CLIENT_IDENTIFIER
                                and not tried_shell_fallback
                            ):
                                LOGGER.debug(
                                    "Grok %s %s -> %s with %s; retrying as %s",
                                    method,
                                    url,
                                    resp.status,
                                    CLIENT_IDENTIFIER,
                                    CLIENT_IDENTIFIER_FALLBACK,
                                )
                                identifier = CLIENT_IDENTIFIER_FALLBACK
                                self._client_identifier = CLIENT_IDENTIFIER_FALLBACK
                                self._client_version = CLIENT_VERSION_FALLBACK
                                tried_shell_fallback = True
                                continue
                            took = elapsed_ms(started)
                            snippet = preview(body)
                            if resp.status == 429:
                                last_status = resp.status
                                last_body = snippet
                                LOGGER.warning(
                                    "Grok %s %s -> 429 in %sms: %s",
                                    method,
                                    url,
                                    took,
                                    snippet,
                                )
                                self._note(f"{method} {candidate} 429: {snippet[:120]}")
                                await _sleep_retry_after(resp, attempt)
                                continue
                            if resp.status == 404:
                                last_status = resp.status
                                last_body = snippet
                                LOGGER.debug("Grok %s %s -> 404 in %sms", method, url, took)
                                break
                            if resp.status >= 400:
                                last_status = resp.status
                                last_body = snippet
                                LOGGER.warning(
                                    "Grok %s %s -> %s in %sms: %s",
                                    method,
                                    url,
                                    resp.status,
                                    took,
                                    snippet,
                                )
                                self._note(f"{method} {candidate} {resp.status}: {snippet[:120]}")
                                if resp.status in RETRY_STATUS:
                                    last_error = HomeAssistantError(
                                        f"Grok rejected the request ({resp.status}): {snippet}"
                                    )
                                    break
                                raise HomeAssistantError(
                                    f"Grok request failed ({resp.status}): {snippet}"
                                )
                            LOGGER.debug(
                                "Grok %s %s -> %s in %sms (%s bytes)",
                                method,
                                url,
                                resp.status,
                                took,
                                len(body),
                            )
                            if raw or (
                                binary_ok and _looks_like_audio(content_type, body)
                            ):
                                self._remember_base(base)
                                return body, content_type
                            if not body:
                                self._remember_base(base)
                                return {}
                            try:
                                parsed = json.loads(body.decode("utf-8"))
                            except (UnicodeDecodeError, json.JSONDecodeError) as err:
                                if binary_ok:
                                    self._remember_base(base)
                                    return body, content_type
                                last_error = err
                                LOGGER.warning(
                                    "Grok %s %s returned non-JSON (%s): %s",
                                    method,
                                    url,
                                    err,
                                    snippet,
                                )
                                break
                            self._remember_base(base)
                            return parsed
                    except (TimeoutError, aiohttp.ClientError) as err:
                        last_error = err
                        LOGGER.warning("Grok transport error on %s %s: %s", method, url, err)
                        break

        if last_status in (401, 403):
            raise ConfigEntryAuthFailed(last_body or "SuperGrok OAuth token was rejected")
        raise HomeAssistantError(
            f"Grok request failed on every endpoint ({last_status or 'network'}): "
            f"{last_body or last_error}"
        )

    async def list_models(self) -> list[str]:
        """Best-effort model catalog from the live account."""
        if self._models_cache is not None:
            return list(self._models_cache)
        try:
            payload = await self._request(
                "GET", CHAT_API_BASES, "/models", preferred_base=self._chat_base, timeout=20
            )
        except HomeAssistantError as err:
            LOGGER.debug("Could not list Grok models: %s", err)
            return []
        items = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        ids: list[str] = []
        for item in items:
            if isinstance(item, str):
                ids.append(item)
            elif isinstance(item, dict) and item.get("id"):
                ids.append(str(item["id"]))
        self._models_cache = ids
        return list(ids)

    def _chat_body(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        temperature: float | None,
        stream: bool,
        response_format: dict[str, Any] | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if temperature is not None:
            body["temperature"] = temperature
        if response_format:
            body["response_format"] = response_format
        return body

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResult:
        """Call chat completions (OpenAI-compatible)."""
        body = self._chat_body(
            model=model,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=False,
            response_format=response_format,
        )
        started = time.monotonic()
        LOGGER.debug(
            "Chat start model=%s messages=%s tools=%s max_tokens=%s",
            model,
            len(messages),
            summarize_tools(tools),
            max_tokens,
        )
        payload = await self._request(
            "POST",
            CHAT_API_BASES,
            "/chat/completions",
            preferred_base=self._chat_base,
            json_data=body,
            timeout=180,
        )
        choices = payload.get("choices") or []
        if not choices:
            LOGGER.error(
                "Chat %s returned no choices in %sms: %s",
                model,
                elapsed_ms(started),
                preview(str(payload)),
            )
            raise HomeAssistantError("Grok returned no chat choices")
        message = (choices[0] or {}).get("message") or {}
        tool_calls = _parse_tool_calls(message.get("tool_calls") or [])
        result = ChatResult(
            text=message.get("content") or "",
            tool_calls=tool_calls,
            finish_reason=choices[0].get("finish_reason"),
            raw=payload,
        )
        LOGGER.debug(
            "Chat %s finish=%s tools_called=%s text_chars=%s in %sms via %s",
            model,
            result.finish_reason,
            [call["name"] for call in tool_calls] or "-",
            len(result.text),
            elapsed_ms(started),
            self._chat_base,
        )
        self._note(f"chat {model} {result.finish_reason} {elapsed_ms(started)}ms")
        return result

    async def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream chat completions as HA-friendly deltas, then a finish event."""
        await self.async_access_token()
        body = self._chat_body(
            model=model,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
            response_format=response_format,
        )
        ordered_bases = self._ordered_bases(CHAT_API_BASES, self._chat_base)
        last_error: Exception | None = None
        started = time.monotonic()
        LOGGER.debug(
            "Chat stream start model=%s messages=%s tools=%s",
            model,
            len(messages),
            summarize_tools(tools),
        )
        for base in ordered_bases:
            url = f"{base.rstrip('/')}/chat/completions"
            identifier = self._client_identifier
            try:
                async with self.session.post(
                    url,
                    headers=self._headers(json_body=True, base=base, identifier=identifier),
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=180),
                ) as resp:
                    if resp.status in (401, 403) and identifier == CLIENT_IDENTIFIER and "grok.com" in base:
                        self._client_identifier = CLIENT_IDENTIFIER_FALLBACK
                        self._client_version = CLIENT_VERSION_FALLBACK
                        last_error = HomeAssistantError(f"Grok stream {resp.status}")
                        continue
                    if resp.status in RETRY_STATUS:
                        last_error = HomeAssistantError(
                            f"Grok stream rejected ({resp.status}): {preview(await resp.read())}"
                        )
                        continue
                    if resp.status >= 400:
                        raise HomeAssistantError(
                            f"Grok stream failed ({resp.status}): {preview(await resp.read())}"
                        )
                    self._remember_base(base)
                    text_chars = 0
                    tool_acc: dict[int, dict[str, str]] = {}
                    finish_reason: str | None = None
                    async for event in _iter_sse(resp):
                        if event is None:
                            break
                        choices = event.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0] or {}
                        finish_reason = choice.get("finish_reason") or finish_reason
                        delta = choice.get("delta") or {}
                        if content := delta.get("content"):
                            text_chars += len(content)
                            yield {"content": content}
                        for call in delta.get("tool_calls") or []:
                            if not isinstance(call, dict):
                                continue
                            index = int(call.get("index") or 0)
                            slot = tool_acc.setdefault(
                                index, {"id": "", "name": "", "arguments": ""}
                            )
                            if call.get("id"):
                                slot["id"] = str(call["id"])
                            function = call.get("function") or {}
                            if function.get("name"):
                                slot["name"] = str(function["name"])
                            if function.get("arguments"):
                                slot["arguments"] += str(function["arguments"])
                    tool_calls = _parse_tool_calls(
                        [
                            {
                                "id": slot["id"] or f"call_{index}",
                                "function": {
                                    "name": slot["name"],
                                    "arguments": slot["arguments"] or "{}",
                                },
                            }
                            for index, slot in sorted(tool_acc.items())
                            if slot["name"]
                        ]
                    )
                    yield {
                        "finish_reason": finish_reason,
                        "tool_calls": tool_calls,
                    }
                    LOGGER.debug(
                        "Chat stream %s finish=%s tools_called=%s text_chars=%s in %sms via %s",
                        model,
                        finish_reason,
                        [call["name"] for call in tool_calls] or "-",
                        text_chars,
                        elapsed_ms(started),
                        base,
                    )
                    self._note(f"chat_stream {model} {finish_reason} {elapsed_ms(started)}ms")
                    return
            except (TimeoutError, aiohttp.ClientError, HomeAssistantError) as err:
                last_error = err
                LOGGER.warning("Grok stream failed on %s: %s", url, err)
                continue
        raise HomeAssistantError(f"Grok chat stream failed: {last_error}")

    async def generate_image(
        self,
        *,
        prompt: str,
        model: str,
        n: int = 1,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
        response_format: str = "url",
        quality: str | None = None,
    ) -> list[ImageResult]:
        """Generate images via /v1/images/generations and /v1/image/generations."""
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "n": n,
            "response_format": response_format,
        }
        if aspect_ratio:
            body["aspect_ratio"] = aspect_ratio
        if resolution:
            body["resolution"] = resolution
        if quality:
            body["quality"] = quality
        started = time.monotonic()
        LOGGER.debug(
            "Imagine start model=%s n=%s ratio=%s res=%s prompt_chars=%s",
            model,
            n,
            aspect_ratio,
            resolution,
            len(prompt),
        )
        payload = await self._request(
            "POST",
            MEDIA_API_BASES,
            IMAGE_PATHS[0],
            preferred_base=self._media_base,
            extra_paths=IMAGE_PATHS[1:],
            json_data=body,
            timeout=180,
        )
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not rows and isinstance(payload, dict) and payload.get("url"):
            rows = [payload]
        if not isinstance(rows, list) or not rows:
            raise HomeAssistantError("Grok Imagine returned no images")
        results: list[ImageResult] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            results.append(
                ImageResult(
                    url=row.get("url"),
                    b64_json=row.get("b64_json"),
                    revised_prompt=row.get("revised_prompt") or row.get("revisedPrompt"),
                    mime_type=row.get("content_type") or "image/jpeg",
                    model=payload.get("model") or model if isinstance(payload, dict) else model,
                )
            )
        if not results:
            raise HomeAssistantError("Grok Imagine returned an empty image payload")
        LOGGER.debug("Imagine %s returned %s image(s) in %sms", model, len(results), elapsed_ms(started))
        return results

    async def list_tts_voices(self) -> list[tuple[str, str]]:
        """Return (voice_id, display_name) from GET /v1/tts/voices."""
        try:
            payload = await self._request(
                "GET",
                MEDIA_API_BASES,
                "/tts/voices",
                preferred_base=self._media_base,
                timeout=20,
            )
        except HomeAssistantError as err:
            LOGGER.debug("Could not list Grok voices: %s", err)
            return []
        voices = payload.get("voices") if isinstance(payload, dict) else payload
        if not isinstance(voices, list):
            return []
        result: list[tuple[str, str]] = []
        for voice in voices:
            if not isinstance(voice, dict):
                continue
            voice_id = voice.get("voice_id") or voice.get("id")
            if not voice_id:
                continue
            result.append((str(voice_id), str(voice.get("name") or voice_id)))
        LOGGER.debug("Listed %s Grok TTS voices", len(result))
        return result

    async def tts(
        self,
        *,
        text: str,
        voice_id: str,
        language: str = "en",
        codec: str = "mp3",
        sample_rate: int = 24000,
        speed: float = 1.0,
    ) -> tuple[bytes, str]:
        """Synthesize speech via POST /v1/tts."""
        body: dict[str, Any] = {
            "text": text,
            "voice_id": voice_id,
            "language": language,
            "speed": speed,
            "output_format": {
                "codec": codec,
                "sample_rate": sample_rate,
            },
        }
        started = time.monotonic()
        LOGGER.debug("TTS start voice=%s lang=%s codec=%s chars=%s", voice_id, language, codec, len(text))
        payload = await self._request(
            "POST",
            MEDIA_API_BASES,
            "/tts",
            preferred_base=self._media_base,
            json_data=body,
            timeout=120,
            binary_ok=True,
        )
        if isinstance(payload, tuple):
            raw, content_type = payload
            LOGGER.debug(
                "TTS voice=%s codec=%s bytes=%s in %sms (raw)",
                voice_id,
                codec,
                len(raw),
                elapsed_ms(started),
            )
            return raw, content_type
        if isinstance(payload, dict) and payload.get("audio"):
            audio = base64.b64decode(payload["audio"])
            content_type = payload.get("content_type") or _content_type_for_codec(codec)
            LOGGER.debug(
                "TTS voice=%s codec=%s bytes=%s in %sms",
                voice_id,
                codec,
                len(audio),
                elapsed_ms(started),
            )
            return audio, content_type
        raise HomeAssistantError("Grok TTS returned no audio")

    async def stt(
        self,
        *,
        audio: bytes,
        filename: str,
        content_type: str,
        language: str | None = None,
        sample_rate: int | None = None,
        raw_pcm: bytes | None = None,
        channels: int = 1,
    ) -> str:
        """Transcribe a finished Assist clip via POST /v1/stt.

        Assist hands us a complete buffer, so the realtime WebSocket is the
        wrong API (sending buffered PCM faster than wall-clock triggers
        xAI's 'past start timer' 400).
        """
        lang = language or "en"
        started = time.monotonic()
        LOGGER.debug(
            "STT start lang=%s rate=%s ch=%s container=%sB pcm=%sB file=%s",
            lang,
            sample_rate,
            channels,
            len(audio),
            len(raw_pcm) if raw_pcm else 0,
            filename,
        )
        attempts: list[tuple[str, aiohttp.FormData]] = []

        if raw_pcm and sample_rate in (8000, 16000, 22050, 24000, 44100, 48000):
            form = aiohttp.FormData()
            form.add_field("audio_format", "pcm")
            form.add_field("sample_rate", str(sample_rate))
            form.add_field("vad_threshold", "0")
            form.add_field("language", lang)
            form.add_field("format", "true")
            if channels > 1:
                form.add_field("channels", str(channels))
            form.add_field(
                "file", raw_pcm, filename="speech.pcm", content_type="application/octet-stream"
            )
            attempts.append((f"pcm {sample_rate}Hz {len(raw_pcm)}B", form))

        form = aiohttp.FormData()
        form.add_field("vad_threshold", "0")
        form.add_field("language", lang)
        form.add_field("format", "true")
        form.add_field("file", audio, filename=filename, content_type=content_type)
        attempts.append((f"wav {len(audio)}B", form))

        last_preview = ""
        for label, form in attempts:
            try:
                payload = await self._request(
                    "POST",
                    MEDIA_API_BASES,
                    "/stt",
                    preferred_base=self._media_base,
                    data=form,
                    timeout=120,
                )
            except HomeAssistantError as err:
                last_preview = str(err)
                LOGGER.warning("STT %s failed: %s", label, err)
                continue
            if text := _extract_transcript(payload):
                LOGGER.debug(
                    "STT %s ok chars=%s duration=%s in %sms: %s",
                    label,
                    len(text),
                    payload.get("duration") if isinstance(payload, dict) else None,
                    elapsed_ms(started),
                    preview(text, 80),
                )
                self._note(f"stt ok {label} {elapsed_ms(started)}ms")
                return text
            duration = payload.get("duration") if isinstance(payload, dict) else None
            snippet = preview(payload if not isinstance(payload, dict) else json.dumps(payload))
            LOGGER.warning(
                "STT %s empty text (duration=%s) in %sms: %s",
                label,
                duration,
                elapsed_ms(started),
                snippet,
            )
            last_preview = f"empty transcript, duration={duration}s"
        self._note(f"stt fail {last_preview}")
        raise HomeAssistantError(
            f"Grok STT heard audio but returned no text ({last_preview})"
        )

    async def create_realtime_client_secret(
        self,
        *,
        model: str,
        expires_seconds: int = 600,
    ) -> dict[str, Any]:
        """Mint an ephemeral Realtime client secret."""
        payload = await self._request(
            "POST",
            MEDIA_API_BASES,
            "/realtime/client_secrets",
            preferred_base=self._media_base,
            json_data={
                "expires_after": {"seconds": min(max(expires_seconds, 30), 3600)},
                "session": {"model": model},
            },
            timeout=30,
        )
        if not isinstance(payload, dict) or not payload.get("value"):
            raise HomeAssistantError("Grok Realtime did not return a client secret")
        return payload

    async def realtime_text(
        self,
        *,
        model: str,
        instructions: str,
        user_text: str,
        tools: list[dict[str, Any]] | None = None,
        voice: str = "eve",
    ) -> ChatResult:
        """Run one text turn over the Realtime WebSocket (text modality)."""
        token = await self.async_access_token()
        last_error: Exception | None = None
        started = time.monotonic()
        LOGGER.debug(
            "Realtime start model=%s tools=%s text_chars=%s",
            model,
            summarize_tools(tools),
            len(user_text),
        )
        for base in REALTIME_WS_URLS:
            url = f"{base}?{urlencode({'model': model})}"
            try:
                async with self.session.ws_connect(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "x-xai-token-auth": GROK_CLI_TOKEN_AUTH,
                        "x-grok-client-identifier": self._client_identifier,
                        "x-grok-client-version": self._client_version,
                    },
                    heartbeat=20,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as ws:
                    session_update: dict[str, Any] = {
                        "type": "session.update",
                        "session": {
                            "voice": voice,
                            "instructions": instructions,
                            "modalities": ["text"],
                            "turn_detection": None,
                        },
                    }
                    if tools:
                        session_update["session"]["tools"] = tools
                    await ws.send_json(session_update)
                    await ws.send_json(
                        {
                            "type": "conversation.item.create",
                            "item": {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": user_text}],
                            },
                        }
                    )
                    await ws.send_json({"type": "response.create"})
                    text_parts: list[str] = []
                    tool_calls: list[dict[str, Any]] = []
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            event = json.loads(msg.data)
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            try:
                                event = json.loads(msg.data.decode("utf-8"))
                            except (UnicodeDecodeError, json.JSONDecodeError):
                                continue
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break
                        else:
                            continue
                        event_type = event.get("type")
                        if event_type in ("response.text.delta", "response.output_text.delta"):
                            if delta := event.get("delta"):
                                text_parts.append(delta)
                        elif event_type == "response.output_audio_transcript.delta":
                            if delta := event.get("delta"):
                                text_parts.append(delta)
                        elif event_type == "response.function_call_arguments.done":
                            args_raw = event.get("arguments") or "{}"
                            try:
                                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                            except json.JSONDecodeError:
                                args = {"_raw": args_raw}
                            tool_calls.append(
                                {
                                    "id": event.get("call_id")
                                    or event.get("id")
                                    or f"call_{len(tool_calls)}",
                                    "name": event.get("name") or "unknown",
                                    "arguments": args if isinstance(args, dict) else {"value": args},
                                }
                            )
                        elif event_type == "error":
                            err = event.get("error") or event
                            raise HomeAssistantError(
                                f"Grok Realtime error: {err.get('message') or err}"
                            )
                        elif event_type == "response.done":
                            break
                    await ws.close()
                    result = ChatResult(text="".join(text_parts).strip(), tool_calls=tool_calls)
                    LOGGER.debug(
                        "Realtime %s tools_called=%s text_chars=%s in %sms via %s",
                        model,
                        [call["name"] for call in tool_calls] or "-",
                        len(result.text),
                        elapsed_ms(started),
                        base,
                    )
                    return result
            except (TimeoutError, aiohttp.ClientError, HomeAssistantError) as err:
                last_error = err
                LOGGER.warning("Realtime websocket failed on %s: %s", url, err)
                continue
        raise HomeAssistantError(f"Grok Realtime websocket failed: {last_error}")


def _parse_tool_calls(raw_calls: list[Any]) -> list[dict[str, Any]]:
    """Normalize OpenAI-shaped tool_calls into {id, name, arguments}."""
    tool_calls: list[dict[str, Any]] = []
    for call in raw_calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        args_raw = function.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except json.JSONDecodeError:
            args = {"_raw": args_raw}
        tool_calls.append(
            {
                "id": call.get("id") or f"call_{len(tool_calls)}",
                "name": function.get("name") or call.get("name") or "unknown",
                "arguments": args if isinstance(args, dict) else {"value": args},
            }
        )
    return tool_calls


async def _iter_sse(resp: aiohttp.ClientResponse) -> AsyncIterator[dict[str, Any] | None]:
    """Yield JSON objects from an OpenAI-style SSE body. None means [DONE]."""
    buffer = ""
    async for raw in resp.content:
        buffer += raw.decode("utf-8", "ignore")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                yield None
                return
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                yield parsed


async def _sleep_retry_after(resp: aiohttp.ClientResponse, attempt: int) -> None:
    """Wait out a 429 using Retry-After or exponential backoff."""
    delay = float(min(2**attempt, 8))
    header = resp.headers.get("Retry-After")
    if header:
        try:
            delay = min(max(float(header), 0.1), 30.0)
        except (TypeError, ValueError):
            pass
    await asyncio.sleep(delay)


def _looks_like_audio(content_type: str, body: bytes) -> bool:
    """True when the gateway returned audio instead of JSON."""
    lowered = (content_type or "").split(";")[0].strip().lower()
    if lowered.startswith("audio/"):
        return True
    stripped = body.lstrip()
    if not stripped:
        return False
    return stripped[:1] not in (b"{", b"[")


def _extract_transcript(payload: Any) -> str | None:
    """Pull transcript text out of the various xAI / OpenAI-shaped bodies."""
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    if not isinstance(payload, dict):
        return None
    for key in ("text", "transcript", "transcription"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if isinstance(payload.get("data"), dict):
        if nested := _extract_transcript(payload["data"]):
            return nested
    if isinstance(payload.get("data"), list) and payload["data"]:
        if nested := _extract_transcript(payload["data"][0]):
            return nested
    words = payload.get("words")
    if isinstance(words, list):
        joined = " ".join(
            str(word.get("text") or "").strip()
            for word in words
            if isinstance(word, dict)
        ).strip()
        if joined:
            return joined
    return None


def _content_type_for_codec(codec: str) -> str:
    return {
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "pcm": "audio/l16",
        "mulaw": "audio/basic",
        "alaw": "audio/PCMA",
    }.get(codec, "application/octet-stream")
