"""JSON helpers that stay on the stdlib. Safe to import from unit tests."""

from __future__ import annotations

import json
import re
from typing import Any

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json_object(text: str) -> Any:
    """Parse JSON, including markdown-fenced or trailing-prose payloads."""
    stripped = (text or "").strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    if match := _JSON_FENCE.search(stripped):
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise json.JSONDecodeError("No JSON object found", stripped, 0)
