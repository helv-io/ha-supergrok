"""JSON extraction helpers. No live xAI or Home Assistant imports."""

from __future__ import annotations

import json

import pytest

from custom_components.supergrok.jsonutil import extract_json_object


def test_extract_json_object_plain() -> None:
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_json_object_fenced() -> None:
    text = "Sure.\n```json\n{\"lamp\": true}\n```\n"
    assert extract_json_object(text) == {"lamp": True}


def test_extract_json_object_embedded() -> None:
    assert extract_json_object('Here you go: {"n": 2} thanks') == {"n": 2}


def test_extract_json_object_rejects_garbage() -> None:
    with pytest.raises(json.JSONDecodeError):
        extract_json_object("not json at all")
