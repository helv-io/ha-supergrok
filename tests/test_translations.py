"""Hassfest-oriented checks for strings and English translations."""

from __future__ import annotations

import json
import re
from pathlib import Path

INTEGRATION = Path(__file__).resolve().parents[1] / "custom_components" / "supergrok"
STRING_FILES = (
    INTEGRATION / "strings.json",
    INTEGRATION / "translations" / "en.json",
)
RE_HTML = re.compile(r"<[^>]+>")
RE_HTTP_URL = re.compile(r"https?://", re.IGNORECASE)


def _string_values(data: object) -> list[str]:
    values: list[str] = []
    if isinstance(data, dict):
        for value in data.values():
            values.extend(_string_values(value))
    elif isinstance(data, list):
        for value in data:
            values.extend(_string_values(value))
    elif isinstance(data, str):
        values.append(data)
    return values


def test_strings_and_en_translations_match() -> None:
    """English translations stay in lockstep with strings.json."""
    strings = json.loads(STRING_FILES[0].read_text(encoding="utf-8"))
    english = json.loads(STRING_FILES[1].read_text(encoding="utf-8"))
    assert strings == english


def test_translations_have_no_html_or_raw_http_urls() -> None:
    """Hassfest rejects HTML / angle-bracket placeholders and raw http(s) URLs."""
    for path in STRING_FILES:
        values = _string_values(json.loads(path.read_text(encoding="utf-8")))
        for value in values:
            assert RE_HTML.search(value) is None, f"HTML in {path.name}: {value}"
            assert "<" not in value and ">" not in value, (
                f"angle bracket in {path.name}: {value}"
            )
            assert RE_HTTP_URL.search(value) is None, f"raw URL in {path.name}: {value}"
            assert "'{" not in value, f"single-quoted placeholder in {path.name}: {value}"
