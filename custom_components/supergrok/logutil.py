"""Safe, compact logging helpers. Never emit access/refresh tokens."""

from __future__ import annotations

import re
import time
from typing import Any

_BEARER = re.compile(r"(?i)(bearer\s+)[a-z0-9._\-+=/]+")
_JWT = re.compile(r"\beyJ[a-zA-Z0-9_\-]{20,}\.[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+")
_HTML = re.compile(r"(?is)<script.*?</script>|<[^>]+>")


def preview(data: bytes | str | None, limit: int = 240) -> str:
    """One-line, redacted body preview for logs."""
    if data is None:
        return ""
    text = data.decode("utf-8", "ignore") if isinstance(data, bytes) else str(data)
    text = _BEARER.sub(r"\1<redacted>", text)
    text = _JWT.sub("<jwt>", text)
    lowered = text.lower()
    if "<html" in lowered or "<!doctype" in lowered:
        if "404" in text:
            return "HTML 404"
        if "403" in text:
            return "HTML 403"
        return "HTML page"
    text = _HTML.sub(" ", text)
    text = " ".join(text.split())
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def elapsed_ms(started: float) -> int:
    """Milliseconds since a monotonic start time."""
    return int((time.monotonic() - started) * 1000)


def summarize_tools(tools: list[dict[str, Any]] | None) -> str:
    """Short tool list for a log line."""
    if not tools:
        return "none"
    names: list[str] = []
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(function, dict) and function.get("name"):
            names.append(str(function["name"]))
        elif isinstance(tool, dict) and tool.get("name"):
            names.append(str(tool["name"]))
    return f"{len(names)}[{','.join(names[:8])}{'…' if len(names) > 8 else ''}]"
