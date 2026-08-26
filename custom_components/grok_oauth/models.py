"""Model catalog for SuperGrok OAuth."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT
from homeassistant.helpers import llm

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry, ConfigSubentry

from .const import (
    CONF_CHAT_MODEL,
    CONF_IMAGE_MODEL,
    CONF_SELECTED_MODELS,
    DEFAULT_AI_TASK_NAME,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_REALTIME_MODEL,
    DEFAULT_VOICE_MODEL,
    MODEL_REALTIME,
    MODEL_VOICE,
    REALTIME_ENABLED,
    SUBENTRY_TYPE_AI_TASK,
    SUBENTRY_TYPE_CONVERSATION,
)

Kind = Literal["chat", "voice", "realtime", "image"]


@dataclass(frozen=True, slots=True)
class GrokModel:
    """A model or capability that can be exposed to Home Assistant."""

    id: str
    label: str
    kind: Kind
    description: str
    ha_platforms: tuple[str, ...]


# Voice is a first-class picker option, not just a chat slug. Realtime stays
# in the catalog so it can be re-enabled via REALTIME_ENABLED. Chat slugs
# become conversation + AI Task entities.
MODEL_CATALOG: tuple[GrokModel, ...] = (
    GrokModel(
        DEFAULT_CHAT_MODEL,
        "Grok 4.6",
        "chat",
        "Current frontier chat / reasoning model",
        ("conversation", "ai_task"),
    ),
    GrokModel(
        "grok-4.5",
        "Grok 4.5",
        "chat",
        "Previous frontier chat model",
        ("conversation", "ai_task"),
    ),
    GrokModel(
        "grok-4.3",
        "Grok 4.3",
        "chat",
        "Long-context chat model",
        ("conversation", "ai_task"),
    ),
    GrokModel(
        "grok-4",
        "Grok 4",
        "chat",
        "Grok 4 alias",
        ("conversation", "ai_task"),
    ),
    GrokModel(
        "grok-4-fast",
        "Grok 4 Fast",
        "chat",
        "Lower-latency Grok 4",
        ("conversation", "ai_task"),
    ),
    GrokModel(
        "grok-build-0.1",
        "Grok Build 0.1",
        "chat",
        "Coding / agent model used by Grok Build",
        ("conversation", "ai_task"),
    ),
    GrokModel(
        "grok-4.20-0309-reasoning",
        "Grok 4.20 Reasoning",
        "chat",
        "Reasoning-tuned 4.20 snapshot",
        ("conversation", "ai_task"),
    ),
    GrokModel(
        "grok-4.20-0309-non-reasoning",
        "Grok 4.20 Non-reasoning",
        "chat",
        "Fast 4.20 snapshot",
        ("conversation", "ai_task"),
    ),
    GrokModel(
        MODEL_VOICE,
        "Voice (TTS + STT)",
        "voice",
        "Grok Voice REST: POST /v1/tts and POST /v1/stt",
        ("tts", "stt"),
    ),
    GrokModel(
        MODEL_REALTIME,
        "Realtime (speech-to-speech)",
        "realtime",
        "Grok Realtime WebSocket: wss://api.x.ai/v1/realtime",
        ("conversation",),
    ),
    GrokModel(
        DEFAULT_IMAGE_MODEL,
        "Grok Imagine 2.0",
        "image",
        "Image generation via /v1/images/generations",
        ("ai_task",),
    ),
    GrokModel(
        "grok-imagine-image",
        "Grok Imagine (fast)",
        "image",
        "Faster Imagine image model",
        ("ai_task",),
    ),
    GrokModel(
        "grok-imagine-image-quality",
        "Grok Imagine Quality",
        "image",
        "Higher-fidelity Imagine image model",
        ("ai_task",),
    ),
)

CATALOG_BY_ID: dict[str, GrokModel] = {model.id: model for model in MODEL_CATALOG}

DEFAULT_SELECTED_MODELS: tuple[str, ...] = (
    DEFAULT_CHAT_MODEL,
    MODEL_VOICE,
    *( (MODEL_REALTIME,) if REALTIME_ENABLED else () ),
    DEFAULT_IMAGE_MODEL,
)


def picker_options() -> list[dict[str, str]]:
    """Return selector options for the multi-picker."""
    return [
        {
            "value": model.id,
            "label": f"{model.label}: {model.description}",
        }
        for model in MODEL_CATALOG
        if REALTIME_ENABLED or model.kind != "realtime"
    ]


def without_withheld_models(selected: list[str] | tuple[str, ...]) -> list[str]:
    """Drop surfaces that are not offered in this release (Realtime)."""
    if REALTIME_ENABLED:
        return list(selected)
    return [item for item in selected if item != MODEL_REALTIME]


def selected_of_kind(selected: list[str] | tuple[str, ...], kind: Kind) -> list[str]:
    """Return selected catalog ids of a given kind."""
    return [item for item in selected if CATALOG_BY_ID.get(item) and CATALOG_BY_ID[item].kind == kind]


def has_voice(selected: list[str] | tuple[str, ...]) -> bool:
    """Return whether Voice was picked."""
    return MODEL_VOICE in selected


def has_realtime(selected: list[str] | tuple[str, ...]) -> bool:
    """Return whether Realtime was picked and is offered in this release."""
    return REALTIME_ENABLED and MODEL_REALTIME in selected


def chat_models(selected: list[str] | tuple[str, ...]) -> list[str]:
    """Return selected chat model slugs."""
    return selected_of_kind(selected, "chat")


def image_models(selected: list[str] | tuple[str, ...]) -> list[str]:
    """Return selected image model slugs."""
    return selected_of_kind(selected, "image")


def first_image_model(selected: list[str] | tuple[str, ...]) -> str | None:
    """Return the first selected image model, if any."""
    models = image_models(selected)
    return models[0] if models else None


def resolve_realtime_model(selected: list[str] | tuple[str, ...]) -> str:
    """Return the realtime model slug to use."""
    if has_realtime(selected):
        return DEFAULT_REALTIME_MODEL
    return DEFAULT_VOICE_MODEL


def resolve_voice_model(selected: list[str] | tuple[str, ...]) -> str:
    """Return the voice-agent model slug."""
    return DEFAULT_VOICE_MODEL


def _merge_selector_options(
    catalog_kind: Kind, extra: list[str] | tuple[str, ...] | None
) -> list[dict[str, str]]:
    """Catalog labels first, then any live ids the account actually has."""
    options = [
        {"value": model.id, "label": model.label}
        for model in MODEL_CATALOG
        if model.kind == catalog_kind
    ]
    known = {item["value"] for item in options}
    for model_id in extra or ():
        if model_id and model_id not in known:
            options.append({"value": model_id, "label": model_id})
            known.add(model_id)
    return options


def chat_model_options(
    extra: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, str]]:
    """Return selector options for conversation / AI Task chat models."""
    return _merge_selector_options("chat", extra)


def image_model_options(
    extra: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, str]]:
    """Return selector options for Imagine models."""
    return _merge_selector_options("image", extra)


def conversation_subentry_data(
    model: str,
    *,
    prompt: str | None = None,
    llm_apis: list[str] | None = None,
) -> dict[str, object]:
    """Return data stored on a conversation subentry."""
    return {
        CONF_CHAT_MODEL: model,
        CONF_PROMPT: prompt if prompt is not None else llm.DEFAULT_INSTRUCTIONS_PROMPT,
        CONF_LLM_HASS_API: list(llm_apis) if llm_apis is not None else [llm.LLM_API_ASSIST],
    }


def config_option(
    entry: ConfigEntry,
    subentry: ConfigSubentry | None,
    key: str,
    default: Any = None,
) -> Any:
    """Read a setting from the subentry, then the parent entry."""
    if subentry is not None and key in subentry.data:
        return subentry.data[key]
    return entry.data.get(key, default)


def conversation_agent_specs(
    entry: ConfigEntry,
) -> list[tuple[ConfigSubentry | None, str, bool]]:
    """Return (subentry, model, default_agent) for each conversation entity.

    Prefer conversation subentries. Fall back to CONF_SELECTED_MODELS so
    pre-subentry installs keep their Grok agents.
    """
    subentries = entry.get_subentries_of_type(SUBENTRY_TYPE_CONVERSATION)
    if subentries:
        return [
            (
                subentry,
                str(subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)),
                index == 0,
            )
            for index, subentry in enumerate(subentries)
        ]
    selected = entry.data.get(CONF_SELECTED_MODELS, [DEFAULT_CHAT_MODEL])
    chats = chat_models(selected) or [DEFAULT_CHAT_MODEL]
    return [(None, model, index == 0) for index, model in enumerate(chats)]


def build_initial_subentries(
    selected: list[str] | tuple[str, ...],
    *,
    prompt: str | None = None,
    llm_apis: list[str] | None = None,
) -> list[dict[str, object]]:
    """Mint conversation + AI Task subentries from the model picker.

    Voice-only installs get no conversation or AI Task subentries.
    """
    chats = chat_models(selected)
    image = first_image_model(selected)
    subentries: list[dict[str, object]] = []
    for model in chats:
        catalog = CATALOG_BY_ID.get(model)
        subentries.append(
            {
                "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                "data": conversation_subentry_data(
                    model, prompt=prompt, llm_apis=llm_apis
                ),
                "title": catalog.label if catalog else DEFAULT_CONVERSATION_NAME,
                "unique_id": None,
            }
        )
    if chats or image:
        ai_task_data: dict[str, object] = {
            CONF_CHAT_MODEL: chats[0] if chats else DEFAULT_CHAT_MODEL,
        }
        if image:
            ai_task_data[CONF_IMAGE_MODEL] = image
        subentries.append(
            {
                "subentry_type": SUBENTRY_TYPE_AI_TASK,
                "data": ai_task_data,
                "title": DEFAULT_AI_TASK_NAME,
                "unique_id": None,
            }
        )
    return subentries
