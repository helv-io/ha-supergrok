"""Authenticated xAI / Grok CLI client used by the HA platforms."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CHAT_API_BASES,
    GROK_CLI_HEADERS,
    IMAGE_PATHS,
    LOGGER,
    MEDIA_API_BASES,
    REALTIME_WS_URLS,
)
from .logutil import elapsed_ms, preview, summarize_tools
from .oauth import GrokOAuthError, OAuthTokens, refresh_tokens

PersistTokens = Callable[[OAuthTokens], Awaitable[None] | None]


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
                raise HomeAssistantError(f"SuperGrok OAuth refresh failed: {err.details or err.reason}") from err
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
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._tokens.access_token}",
            "Accept": "application/json",
        }
        if base and "grok.com" in base:
            headers.update(GROK_CLI_HEADERS)
        if json_body:
            headers["Content-Type"] = "application/json"
        if extra:
            headers.update(extra)
        return headers

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
        extra_paths: tuple[str, ...] = (),
    ) -> Any:
        """Issue an authenticated request, trying hosts / path aliases."""
        await self.async_access_token()
        ordered_bases = []
        if preferred_base:
            ordered_bases.append(preferred_base)
        for base in bases:
            if base not in ordered_bases:
                ordered_bases.append(base)
        paths = (path, *extra_paths)
        last_error: Exception | None = None
        last_status = 0
        last_body = ""

        for base in ordered_bases:
            for candidate in paths:
                url = f"{base.rstrip('/')}{candidate}"
                started = time.monotonic()
                try:
                    async with self.session.request(
                        method,
                        url,
                        headers=self._headers(json_body=json_data is not None, base=base),
                        json=json_data,
                        data=data,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                    ) as resp:
                        body = await resp.read()
                        if resp.status in (401, 403) and base == ordered_bases[0] and candidate == paths[0]:
                            LOGGER.info("Grok %s %s -> %s; refreshing token and retrying", method, url, resp.status)
                            self._tokens.expires_at = 0
                            await self.async_access_token()
                            async with self.session.request(
                                method,
                                url,
                                headers=self._headers(json_body=json_data is not None, base=base),
                                json=json_data,
                                data=data,
                                params=params,
                                timeout=aiohttp.ClientTimeout(total=timeout),
                            ) as retry:
                                body = await retry.read()
                                resp = retry
                        took = elapsed_ms(started)
                        snippet = preview(body)
                        if resp.status == 404:
                            last_status = resp.status
                            last_body = snippet
                            LOGGER.debug("Grok %s %s -> 404 in %sms", method, url, took)
                            continue
                        if resp.status >= 400:
                            last_status = resp.status
                            last_body = snippet
                            LOGGER.warning("Grok %s %s -> %s in %sms: %s", method, url, resp.status, took, snippet)
                            self._note(f"{method} {candidate} {resp.status}: {snippet[:120]}")
                            if resp.status in (401, 402, 403):
                                last_error = HomeAssistantError(
                                    f"Grok rejected the request ({resp.status}): {snippet}"
                                )
                                continue
                            raise HomeAssistantError(
                                f"Grok request failed ({resp.status}): {snippet}"
                            )
                        LOGGER.debug("Grok %s %s -> %s in %sms (%s bytes)", method, url, resp.status, took, len(body))
                        if raw:
                            if base in CHAT_API_BASES:
                                self._chat_base = base
                            else:
                                self._media_base = base
                            return body, resp.headers.get("Content-Type", "application/octet-stream")
                        if not body:
                            if base in CHAT_API_BASES:
                                self._chat_base = base
                            else:
                                self._media_base = base
                            return {}
                        try:
                            parsed = json.loads(body.decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError) as err:
                            last_error = err
                            LOGGER.warning("Grok %s %s returned non-JSON (%s): %s", method, url, err, snippet)
                            continue
                        if base in CHAT_API_BASES:
                            self._chat_base = base
                        else:
                            self._media_base = base
                        return parsed
                except (TimeoutError, aiohttp.ClientError) as err:
                    last_error = err
                    LOGGER.warning("Grok transport error on %s %s: %s", method, url, err)
                    continue

        if last_status in (401, 403):
            raise ConfigEntryAuthFailed(last_body or "SuperGrok OAuth token was rejected")
        raise HomeAssistantError(
            f"Grok request failed on every endpoint ({last_status or 'network'}): "
            f"{last_body or last_error}"
        )

    async def list_models(self) -> list[str]:
        """Best-effort model catalog from the live account."""
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
        return ids

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> ChatResult:
        """Call chat completions (OpenAI-compatible)."""
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if temperature is not None:
            body["temperature"] = temperature
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
            LOGGER.error("Chat %s returned no choices in %sms: %s", model, elapsed_ms(started), preview(str(payload)))
            raise HomeAssistantError("Grok returned no chat choices")
        message = (choices[0] or {}).get("message") or {}
        tool_calls: list[dict[str, Any]] = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            args_raw = function.get("arguments") or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {"_raw": args_raw}
            tool_calls.append(
                {
                    "id": call.get("id") or f"call_{len(tool_calls)}",
                    "name": function.get("name") or "unknown",
                    "arguments": args if isinstance(args, dict) else {"value": args},
                }
            )
        result = ChatResult(
            text=message.get("content") or "",
            tool_calls=tool_calls,
            finish_reason=choices[0].get("finish_reason"),
            raw=payload,
        )
        LOGGER.info(
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
        LOGGER.debug("Imagine start model=%s n=%s ratio=%s res=%s prompt_chars=%s", model, n, aspect_ratio, resolution, len(prompt))
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
        LOGGER.info("Imagine %s returned %s image(s) in %sms", model, len(results), elapsed_ms(started))
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
        try:
            payload = await self._request(
                "POST",
                MEDIA_API_BASES,
                "/tts",
                preferred_base=self._media_base,
                json_data=body,
                timeout=120,
            )
        except HomeAssistantError:
            # Some gateways return raw audio instead of JSON.
            raw, content_type = await self._request(
                "POST",
                MEDIA_API_BASES,
                "/tts",
                preferred_base=self._media_base,
                json_data=body,
                timeout=120,
                raw=True,
            )
            LOGGER.info("TTS voice=%s codec=%s bytes=%s in %sms (raw)", voice_id, codec, len(raw), elapsed_ms(started))
            return raw, content_type

        if isinstance(payload, dict) and payload.get("audio"):
            audio = base64.b64decode(payload["audio"])
            content_type = payload.get("content_type") or _content_type_for_codec(codec)
            LOGGER.info("TTS voice=%s codec=%s bytes=%s in %sms", voice_id, codec, len(audio), elapsed_ms(started))
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
            await self.async_access_token()
            try:
                async with self.session.post(
                    "https://api.x.ai/v1/stt",
                    headers=self._headers(json_body=False, base="https://api.x.ai/v1"),
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    body = await resp.read()
            except (TimeoutError, aiohttp.ClientError) as err:
                last_preview = str(err)
                LOGGER.warning("Grok STT %s transport error: %s", label, err)
                continue
            snippet = preview(body)
            last_preview = f"{resp.status} {snippet}"
            if resp.status >= 400:
                LOGGER.warning("STT %s -> %s in %sms: %s", label, resp.status, elapsed_ms(started), snippet)
                continue
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                LOGGER.warning("STT %s returned non-JSON: %s", label, snippet)
                continue
            if text := _extract_transcript(payload):
                LOGGER.info(
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
            LOGGER.warning(
                "STT %s HTTP %s empty text (duration=%s) in %sms: %s",
                label,
                resp.status,
                duration,
                elapsed_ms(started),
                preview(preview),
            )
            last_preview = f"HTTP {resp.status} empty transcript, duration={duration}s"
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
        LOGGER.debug("Realtime start model=%s tools=%s text_chars=%s", model, summarize_tools(tools), len(user_text))
        for base in REALTIME_WS_URLS:
            url = f"{base}?{urlencode({'model': model})}"
            try:
                async with self.session.ws_connect(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        **GROK_CLI_HEADERS,
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
                                    "id": event.get("call_id") or event.get("id") or f"call_{len(tool_calls)}",
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
                    LOGGER.info(
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
