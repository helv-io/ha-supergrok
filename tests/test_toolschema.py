"""Tool schema conversion and tool-call argument parsing. No live xAI."""

from __future__ import annotations

import json

import voluptuous as vol

from custom_components.supergrok.toolschema import (
    _type_from_voluptuous_value,
    accumulate_stream_tool_delta,
    convert_tool_parameters,
    missing_required_properties,
    parse_tool_arguments,
    parse_tool_calls,
    sanitize_tool_schema,
    schema_from_voluptuous,
)

BABY_BUDDY_CREATE_DIAPER = {
    "type": "object",
    "properties": {
        "child_id": {
            "type": "integer",
            "description": "ID of the child. Use list_children to get IDs.",
        },
        "time": {
            "type": "string",
            "description": "Time of the change in ISO 8601 format",
        },
        "wet": {"type": "boolean", "description": "Whether the diaper was wet"},
        "solid": {"type": "boolean", "description": "Whether the diaper had solid contents"},
        "color": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "Stool color",
        },
        "notes": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "default": None,
        },
    },
    "required": ["child_id", "time", "wet", "solid"],
    "additionalProperties": False,
}


def test_sanitize_keeps_required_on_object_schema() -> None:
    """MCP object schemas must still advertise required properties to xAI."""
    sanitized = sanitize_tool_schema(BABY_BUDDY_CREATE_DIAPER)
    assert sanitized["type"] == "object"
    assert "child_id" in sanitized["properties"]
    assert sanitized["required"] == ["child_id", "time", "wet", "solid"]
    assert sanitized["properties"]["color"]["type"] == "string"


def test_sanitize_flattens_root_anyof_instead_of_emptying() -> None:
    """Root anyOf used to become {type: object, properties: {}} and force {} args."""
    schema = {
        "anyOf": [
            {
                "type": "object",
                "properties": {
                    "child_id": {"type": "integer"},
                    "wet": {"type": "boolean"},
                },
                "required": ["child_id"],
            }
        ]
    }
    sanitized = sanitize_tool_schema(schema)
    assert sanitized["properties"]["child_id"]["type"] == "integer"
    assert sanitized["required"] == ["child_id"]
    assert sanitized != {"type": "object", "properties": {}}


def test_sanitize_resolves_local_ref_for_required_field() -> None:
    schema = {
        "type": "object",
        "properties": {"child_id": {"$ref": "#/$defs/ChildId"}},
        "required": ["child_id"],
        "$defs": {"ChildId": {"type": "integer", "description": "Child primary key"}},
    }
    sanitized = sanitize_tool_schema(schema)
    assert sanitized["properties"]["child_id"]["type"] == "integer"
    assert sanitized["required"] == ["child_id"]


def test_voluptuous_required_survives_convert() -> None:
    """HA MCP tools are voluptuous Schemas; required markers must reach xAI."""
    parameters = vol.Schema(
        {
            vol.Required("child_id", description="ID of the child"): int,
            vol.Required("wet"): bool,
            vol.Optional("notes"): str,
        }
    )
    converted = convert_tool_parameters(parameters)
    assert converted["type"] == "object"
    assert "child_id" in converted["properties"]
    assert "child_id" in converted.get("required", [])
    assert "wet" in converted["required"]
    assert missing_required_properties(converted, {}) == ["child_id", "wet"]
    assert missing_required_properties(converted, {"child_id": 1, "wet": True}) == []


def test_schema_from_voluptuous_reads_required_marker_by_class_name() -> None:
    """Probatio Required is not always isinstance(vol.Required)."""
    parameters = vol.Schema({vol.Required("child_id"): int, vol.Optional("color"): str})
    fallback = schema_from_voluptuous(parameters)
    assert fallback["required"] == ["child_id"]
    assert fallback["properties"]["child_id"]["type"] == "integer"


def test_parse_tool_arguments_openai_function() -> None:
    args = parse_tool_arguments(
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "babybuddy__babybuddy-diapers_create_diaper_change",
                "arguments": '{"child_id": 1, "wet": true, "solid": false}',
            },
        }
    )
    assert args == {"child_id": 1, "wet": True, "solid": False}


def test_parse_tool_arguments_top_level_responses_shape() -> None:
    """Name used to fall back to call.name while arguments stayed {}."""
    parsed = parse_tool_calls(
        [
            {
                "id": "call_1",
                "name": "babybuddy__babybuddy-diapers_create_diaper_change",
                "arguments": '{"child_id": 1, "time": "2026-09-05T15:00:00", "wet": true, "solid": false}',
            }
        ]
    )
    assert parsed[0]["name"] == "babybuddy__babybuddy-diapers_create_diaper_change"
    assert parsed[0]["arguments"]["child_id"] == 1
    assert parsed[0]["arguments"]["wet"] is True


def test_parse_tool_arguments_already_an_object() -> None:
    args = parse_tool_arguments({"function": {"arguments": {"child_id": 1}}})
    assert args == {"child_id": 1}


def test_accumulate_stream_reads_top_level_arguments() -> None:
    slot = {"id": "", "name": "", "arguments": ""}
    accumulate_stream_tool_delta(
        slot,
        {
            "index": 0,
            "id": "call_1",
            "name": "create_diaper_change",
            "arguments": '{"child_id": 1}',
        },
    )
    assert slot["name"] == "create_diaper_change"
    assert json.loads(slot["arguments"]) == {"child_id": 1}


def test_list_validator_does_not_raise_unhashable_type() -> None:
    """Baby Buddy MCP uses list validators (tags, type unions). 0.6.5 hashed them."""
    parameters = vol.Schema(
        {
            vol.Required("child_id"): int,
            vol.Required("time"): str,
            vol.Required("wet"): bool,
            vol.Required("solid"): bool,
            vol.Optional("color"): vol.Any(str, None),
            vol.Optional("tags"): [str],
        }
    )
    converted = convert_tool_parameters(parameters)
    assert converted["type"] == "object"
    assert "child_id" in converted["properties"]
    assert converted["required"] == ["child_id", "time", "wet", "solid"]
    assert converted["properties"]["tags"]["type"] == "array"
    assert converted["properties"]["color"]["type"] == "string"
    assert missing_required_properties(converted, {}) == [
        "child_id",
        "time",
        "wet",
        "solid",
    ]


def test_type_from_voluptuous_list_and_type_union() -> None:
    """A list schema value must not raise TypeError: cannot use 'list' as a dict key."""
    assert _type_from_voluptuous_value([str]) == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert _type_from_voluptuous_value(["string", "null"]) == {"type": "string"}
    assert _type_from_voluptuous_value([int, None]) == {"type": "integer"}


def test_schema_from_voluptuous_survives_list_field_when_convert_skipped(
    monkeypatch,
) -> None:
    """format_tools always builds the voluptuous fallback before convert."""
    monkeypatch.setattr(
        "custom_components.supergrok.toolschema._convert_voluptuous",
        lambda *_args, **_kwargs: None,
    )
    parameters = vol.Schema(
        {
            vol.Required("child_id"): int,
            vol.Optional("tags"): [str],
        }
    )
    converted = convert_tool_parameters(parameters)
    assert converted["required"] == ["child_id"]
    assert converted["properties"]["tags"]["type"] == "array"


def test_convert_failure_still_advertises_voluptuous_required(monkeypatch) -> None:
    """A convert exception must not advertise an empty object schema."""
    monkeypatch.setattr(
        "custom_components.supergrok.toolschema._convert_voluptuous",
        lambda *_args, **_kwargs: None,
    )
    parameters = vol.Schema({vol.Required("child_id"): int, vol.Required("wet"): bool})
    converted = convert_tool_parameters(parameters)
    assert converted["properties"]["child_id"]["type"] == "integer"
    assert converted["required"] == ["child_id", "wet"]


def test_accumulate_stream_reads_dict_arguments() -> None:
    slot = {"id": "", "name": "", "arguments": ""}
    accumulate_stream_tool_delta(
        slot,
        {"function": {"name": "create_diaper_change", "arguments": {"child_id": 1}}},
    )
    assert json.loads(slot["arguments"]) == {"child_id": 1}
