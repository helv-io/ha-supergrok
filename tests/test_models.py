"""Model picker and Realtime gate. No live xAI."""

from __future__ import annotations

from custom_components.grok_oauth.const import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_IMAGE_MODEL,
    MODEL_REALTIME,
    MODEL_VOICE,
    REALTIME_ENABLED,
)
from custom_components.grok_oauth.models import (
    DEFAULT_SELECTED_MODELS,
    has_realtime,
    picker_options,
    without_withheld_models,
)


def test_realtime_is_withheld_from_the_picker() -> None:
    """New and existing setups cannot select Realtime in this release."""
    assert REALTIME_ENABLED is False
    values = [option["value"] for option in picker_options()]
    assert MODEL_REALTIME not in values
    assert DEFAULT_CHAT_MODEL in values
    assert MODEL_VOICE in values
    assert DEFAULT_IMAGE_MODEL in values
    assert MODEL_REALTIME not in DEFAULT_SELECTED_MODELS


def test_has_realtime_stays_off_when_legacy_config_lists_it() -> None:
    """A leftover selected_models value must not enable Realtime."""
    assert has_realtime(["grok-4.6", "voice", "realtime"]) is False
    assert without_withheld_models(["grok-4.6", "voice", "realtime"]) == [
        "grok-4.6",
        "voice",
    ]
