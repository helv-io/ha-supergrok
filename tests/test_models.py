"""Model picker and Realtime gate. No live xAI."""

from __future__ import annotations

from custom_components.grok_oauth.const import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_IMAGE_MODEL,
    MODEL_REALTIME,
    MODEL_VOICE,
    REALTIME_ENABLED,
    SUBENTRY_TYPE_AI_TASK,
    SUBENTRY_TYPE_CONVERSATION,
)
from custom_components.grok_oauth.models import (
    DEFAULT_SELECTED_MODELS,
    build_initial_subentries,
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


def test_voice_only_mints_no_chat_subentries() -> None:
    """Voice-only installs do not invent a grok-4.6 conversation or AI Task."""
    assert build_initial_subentries([MODEL_VOICE]) == []


def test_build_initial_subentries_covers_chat_and_ai_task() -> None:
    """First-time setup mints one conversation per chat model plus AI Task."""
    subentries = build_initial_subentries(
        ["grok-4.6", "grok-4.5", "voice", DEFAULT_IMAGE_MODEL],
        prompt="Hello",
    )
    types = [item["subentry_type"] for item in subentries]
    assert types == [
        SUBENTRY_TYPE_CONVERSATION,
        SUBENTRY_TYPE_CONVERSATION,
        SUBENTRY_TYPE_AI_TASK,
    ]
    assert subentries[0]["data"]["chat_model"] == "grok-4.6"
    assert subentries[0]["data"]["prompt"] == "Hello"
    assert subentries[1]["data"]["chat_model"] == "grok-4.5"
    assert subentries[2]["data"]["image_model"] == DEFAULT_IMAGE_MODEL
