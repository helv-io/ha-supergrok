"""Tool schema conversion and tool-call argument parsing. No live xAI."""

from __future__ import annotations

import json

import voluptuous as vol

from custom_components.supergrok.toolschema import (
    _type_from_voluptuous_value,
    accumulate_stream_tool_delta,
    coerce_arguments_to_schema,
    convert_tool_parameters,
    missing_required_properties,
    parse_tool_arguments,
    parse_tool_calls,
    prepare_tool_call,
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


def test_convert_and_sanitize_keep_child_id_integer() -> None:
    """Integer MCP fields must stay type integer through convert, sanitize, fallback."""
    parameters = vol.Schema(
        {
            vol.Required("child_id"): int,
            vol.Required("wet"): bool,
            vol.Optional("amount"): float,
        }
    )
    converted = convert_tool_parameters(parameters)
    assert converted["properties"]["child_id"]["type"] == "integer"
    assert converted["properties"]["wet"]["type"] == "boolean"
    assert converted["properties"]["amount"]["type"] == "number"

    sanitized = sanitize_tool_schema(BABY_BUDDY_CREATE_DIAPER)
    assert sanitized["properties"]["child_id"]["type"] == "integer"

    type_list = sanitize_tool_schema(
        {
            "type": "object",
            "properties": {"child_id": {"type": ["integer", "null"]}},
            "required": ["child_id"],
        }
    )
    assert type_list["properties"]["child_id"]["type"] == "integer"

    string_first_union = sanitize_tool_schema(
        {
            "type": "object",
            "properties": {
                "child_id": {"anyOf": [{"type": "string"}, {"type": "integer"}]}
            },
            "required": ["child_id"],
        }
    )
    assert string_first_union["properties"]["child_id"]["type"] == "integer"


def test_fallback_restores_integer_when_convert_emits_string(monkeypatch) -> None:
    """A convert result that marks child_id as string must not be advertised."""
    monkeypatch.setattr(
        "custom_components.supergrok.toolschema._convert_voluptuous",
        lambda *_args, **_kwargs: {
            "type": "object",
            "properties": {"child_id": {"type": "string", "description": "ID"}},
            "required": ["child_id"],
        },
    )
    parameters = vol.Schema({vol.Required("child_id"): int})
    converted = convert_tool_parameters(parameters)
    assert converted["properties"]["child_id"]["type"] == "integer"
    assert converted["required"] == ["child_id"]


def test_coerce_string_digits_to_integer_required_field() -> None:
    """Grok often sends child_id as \"1\"; MCP requires integer."""
    schema = {
        "type": "object",
        "properties": {
            "child_id": {"type": "integer"},
            "wet": {"type": "boolean"},
            "amount": {"type": "number"},
        },
        "required": ["child_id"],
    }
    coerced = coerce_arguments_to_schema(
        schema, {"child_id": "1", "wet": "true", "amount": "2.5"}
    )
    assert coerced == {"child_id": 1, "wet": True, "amount": 2.5}
    assert isinstance(coerced["child_id"], int)
    assert missing_required_properties(schema, coerced) == []


def test_coerce_leaves_non_numeric_strings_alone() -> None:
    schema = {
        "type": "object",
        "properties": {
            "child_id": {"type": "integer"},
            "wet": {"type": "boolean"},
            "notes": {"type": "string"},
        },
        "required": ["child_id"],
    }
    original = {"child_id": "Arthur", "wet": "yes", "notes": "1"}
    assert coerce_arguments_to_schema(schema, original) == original
    assert coerce_arguments_to_schema(schema, {"child_id": "1.5"}) == {"child_id": "1.5"}
    assert coerce_arguments_to_schema(schema, {}) == {}
    assert missing_required_properties(schema, {}) == ["child_id"]


class _FakeJsonInteger:
    """Stand-in for Probatio ``JsonInteger()`` (class name ``_JsonNumberType``)."""

    def __init__(self, integer: bool = True) -> None:
        self.integer = integer

    def __repr__(self) -> str:
        return "JsonInteger()" if self.integer else "JsonNumber()"


_FakeJsonInteger.__name__ = "_JsonNumberType"


def test_type_from_voluptuous_recognizes_probatio_json_integer() -> None:
    """HA MCP from_openapi stores type:integer as JsonInteger, not Python int."""
    assert _type_from_voluptuous_value(_FakeJsonInteger(True)) == {"type": "integer"}
    assert _type_from_voluptuous_value(_FakeJsonInteger(False)) == {"type": "number"}


def test_convert_mcp_from_openapi_keeps_child_id_integer() -> None:
    """Live HA MCP path: from_openapi(integer) then convert+sanitize+restore."""
    from probatio import from_openapi

    parameters = from_openapi(BABY_BUDDY_CREATE_DIAPER)
    converted = convert_tool_parameters(parameters)
    assert converted["properties"]["child_id"]["type"] == "integer"
    assert converted["required"] == ["child_id", "time", "wet", "solid"]

    fallback = schema_from_voluptuous(parameters)
    assert fallback["properties"]["child_id"]["type"] == "integer"


def test_coerce_uses_source_integer_when_advertised_schema_says_string() -> None:
    """0.6.7 advertised JsonInteger as string, so schema-only coerce was a no-op."""
    from probatio import from_openapi

    source = from_openapi(BABY_BUDDY_CREATE_DIAPER)
    advertised = {
        "type": "object",
        "properties": {
            "child_id": {"type": "string"},
            "wet": {"type": "boolean"},
            "solid": {"type": "boolean"},
            "time": {"type": "string"},
        },
        "required": ["child_id", "time", "wet", "solid"],
    }
    coerced = coerce_arguments_to_schema(
        advertised,
        {"child_id": "1", "time": "2026-09-05T15:00:00", "wet": True, "solid": False},
        source=source,
    )
    assert coerced["child_id"] == 1
    assert type(coerced["child_id"]) is int


def test_coerce_leaves_string_entity_id_digits_alone() -> None:
    """id-like keys stay strings when neither schema nor source marks integer."""
    schema = {
        "type": "object",
        "properties": {"entity_id": {"type": "string"}},
    }
    parameters = vol.Schema({vol.Required("entity_id"): str})
    assert coerce_arguments_to_schema(
        schema, {"entity_id": "1"}, source=parameters
    ) == {"entity_id": "1"}


def test_prepare_tool_call_coerces_live_string_child_id() -> None:
    """Live failure: Grok sends child_id \"1\"; MCP dispatch must keep a Python int."""
    from probatio import from_openapi

    tool_name = "babybuddy__babybuddy-diapers_create_diaper_change"
    parameters = from_openapi(BABY_BUDDY_CREATE_DIAPER)
    advertised = convert_tool_parameters(parameters)
    assert advertised["properties"]["child_id"]["type"] == "integer"

    args, missing = prepare_tool_call(
        {
            "id": "call_1",
            "name": tool_name,
            "arguments": {
                "child_id": "1",
                "time": "2026-09-05T15:00:00",
                "wet": True,
                "solid": False,
            },
        },
        {tool_name: advertised},
        {tool_name: parameters},
    )
    assert missing == []
    assert args["child_id"] == 1
    assert type(args["child_id"]) is int
    assert args["wet"] is True


def test_prepare_tool_call_coerces_when_advertised_type_is_string() -> None:
    """Dispatch helper still coerces if convert advertised string (0.6.7)."""
    from probatio import from_openapi

    tool_name = "babybuddy__babybuddy-diapers_create_diaper_change"
    parameters = from_openapi(BABY_BUDDY_CREATE_DIAPER)
    advertised = {
        "type": "object",
        "properties": {
            "child_id": {"type": "string"},
            "time": {"type": "string"},
            "wet": {"type": "boolean"},
            "solid": {"type": "boolean"},
        },
        "required": ["child_id", "time", "wet", "solid"],
    }
    args, missing = prepare_tool_call(
        {
            "id": "call_1",
            "name": tool_name,
            "arguments": {
                "child_id": "1",
                "time": "2026-09-05T15:00:00",
                "wet": True,
                "solid": False,
            },
        },
        {tool_name: advertised},
        {tool_name: parameters},
    )
    assert missing == []
    assert type(args["child_id"]) is int
    assert args["child_id"] == 1
