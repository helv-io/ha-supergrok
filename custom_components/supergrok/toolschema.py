"""Convert HA / MCP tool schemas and tool-call payloads for xAI.

xAI compiles a tool-call grammar from the advertised JSON Schema and treats
the schema as strict (additionalProperties defaults to false). If SuperGrok
advertises ``{"type": "object", "properties": {}}`` the only legal arguments
are ``{}``, so required MCP fields such as child_id can never be generated.
Prompt text cannot override that grammar.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from voluptuous_openapi import convert

from .const import LOGGER

_UNSUPPORTED_SCHEMA_KEYS = {
    "oneOf",
    "anyOf",
    "allOf",
    "not",
    "const",
    "if",
    "then",
    "else",
    "$ref",
    "$defs",
    "definitions",
    "prefixItems",
    "unevaluatedProperties",
    "patternProperties",
}

_TYPE_NAMES = {
    int: "integer",
    str: "string",
    float: "number",
    bool: "boolean",
}


def parse_tool_arguments(call: Mapping[str, Any]) -> dict[str, Any]:
    """Read tool arguments from OpenAI or Responses-shaped tool_call objects."""
    function = call.get("function") if isinstance(call.get("function"), Mapping) else {}
    args_raw = function.get("arguments") if isinstance(function, Mapping) else None
    if args_raw in (None, ""):
        args_raw = call.get("arguments")
    if args_raw in (None, ""):
        args_raw = call.get("input")
    if args_raw in (None, ""):
        return {}
    if isinstance(args_raw, Mapping):
        return dict(args_raw)
    if not isinstance(args_raw, str):
        return {"value": args_raw}
    try:
        parsed = json.loads(args_raw)
    except json.JSONDecodeError:
        return {"_raw": args_raw}
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


def parse_tool_name(call: Mapping[str, Any]) -> str:
    """Read the function name from nested or top-level tool_call shapes."""
    function = call.get("function") if isinstance(call.get("function"), Mapping) else {}
    name = None
    if isinstance(function, Mapping):
        name = function.get("name")
    return str(name or call.get("name") or "unknown")


def parse_tool_calls(raw_calls: list[Any]) -> list[dict[str, Any]]:
    """Normalize OpenAI-shaped tool_calls into {id, name, arguments}."""
    tool_calls: list[dict[str, Any]] = []
    for call in raw_calls:
        if not isinstance(call, Mapping):
            continue
        tool_calls.append(
            {
                "id": call.get("id") or f"call_{len(tool_calls)}",
                "name": parse_tool_name(call),
                "arguments": parse_tool_arguments(call),
            }
        )
    return tool_calls


def accumulate_stream_tool_delta(slot: dict[str, str], call: Mapping[str, Any]) -> None:
    """Merge one streamed tool-call fragment into an accumulator slot."""
    if call.get("id"):
        slot["id"] = str(call["id"])
    function = call.get("function") if isinstance(call.get("function"), Mapping) else {}
    name = None
    if isinstance(function, Mapping):
        name = function.get("name")
    name = name or call.get("name")
    if name:
        slot["name"] = str(name)
    args_piece = function.get("arguments") if isinstance(function, Mapping) else None
    if args_piece in (None, ""):
        args_piece = call.get("arguments")
    if args_piece in (None, ""):
        return
    if isinstance(args_piece, Mapping):
        slot["arguments"] = json.dumps(dict(args_piece))
        return
    slot["arguments"] += str(args_piece)


def required_properties(schema: Mapping[str, Any] | None) -> list[str]:
    """Return required property names advertised on a parameters schema."""
    if not isinstance(schema, Mapping):
        return []
    required = schema.get("required")
    if not isinstance(required, list):
        return []
    properties = schema.get("properties")
    names = {name for name in (properties or {}) if isinstance(name, str)}
    return [
        name
        for name in required
        if isinstance(name, str) and (not names or name in names)
    ]


def missing_required_properties(
    schema: Mapping[str, Any] | None, arguments: Mapping[str, Any] | None
) -> list[str]:
    """Return required property names that are absent from a tool-call payload."""
    args = arguments if isinstance(arguments, Mapping) else {}
    return [name for name in required_properties(schema) if name not in args]


def schema_from_voluptuous(parameters: Any) -> dict[str, Any]:
    """Best-effort object schema from a voluptuous / Probatio mapping."""
    raw = getattr(parameters, "schema", parameters)
    if not isinstance(raw, Mapping):
        return {"type": "object", "properties": {}}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for key, value in raw.items():
        name = getattr(key, "schema", key)
        if not isinstance(name, str):
            continue
        prop = _type_from_voluptuous_value(value)
        description = getattr(key, "description", None)
        if description:
            prop["description"] = description
        properties[name] = prop
        if type(key).__name__ == "Required":
            required.append(name)
    result: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        result["required"] = required
    return result


def convert_tool_parameters(
    parameters: Any,
    custom_serializer: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    """Convert a voluptuous tool schema to an xAI-safe JSON Schema object."""
    fallback = schema_from_voluptuous(parameters)
    converted = _convert_voluptuous(parameters, custom_serializer)
    raw = converted if isinstance(converted, dict) else fallback
    sanitized = sanitize_tool_schema(raw)
    return _restore_from_fallback(sanitized, fallback)


def sanitize_tool_schema(schema: Any) -> dict[str, Any]:
    """xAI requires a parameters root that is an object (not array/scalar/union)."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    schema = _resolve_local_refs(deepcopy(schema))
    schema = _flatten_object_union(schema)

    for key in _UNSUPPORTED_SCHEMA_KEYS:
        schema.pop(key, None)

    schema_type = schema.get("type")
    if schema_type != "object":
        if "properties" not in schema:
            return {"type": "object", "properties": {}}
        schema["type"] = "object"

    properties = schema.setdefault("properties", {})
    if not isinstance(properties, dict):
        schema["properties"] = {}
        properties = schema["properties"]

    for name, prop in list(properties.items()):
        properties[name] = _sanitize_property(prop)

    required = schema.get("required")
    if required is not None and not isinstance(required, list):
        schema.pop("required")
        required = None
    if isinstance(required, list):
        schema["required"] = [
            item for item in required if isinstance(item, str) and item in properties
        ]
        if not schema["required"]:
            schema.pop("required")
    return schema


def _restore_from_fallback(
    sanitized: dict[str, Any], fallback: dict[str, Any]
) -> dict[str, Any]:
    """Re-apply voluptuous properties/required if convert or sanitize dropped them."""
    fallback_props = fallback.get("properties")
    if isinstance(fallback_props, dict) and fallback_props and not sanitized.get("properties"):
        sanitized["properties"] = {
            name: _sanitize_property(prop) for name, prop in fallback_props.items()
        }
    props = sanitized.get("properties") or {}
    fallback_required = required_properties(fallback)
    if fallback_required and not sanitized.get("required"):
        restored = [name for name in fallback_required if name in props]
        if restored:
            sanitized["required"] = restored
    return sanitized


def _convert_voluptuous(
    parameters: Any,
    custom_serializer: Callable[[Any], Any] | None,
) -> dict[str, Any] | None:
    """Prefer Home Assistant's Probatio codec, then voluptuous-openapi."""
    converters: list[Callable[..., Any]] = []
    try:
        from probatio import to_openapi

        converters.append(to_openapi)
    except ImportError:
        pass
    converters.append(convert)
    last_error: Exception | None = None
    for converter in converters:
        try:
            raw = converter(parameters, custom_serializer=custom_serializer)
        except Exception as err:  # noqa: BLE001
            last_error = err
            continue
        if isinstance(raw, dict):
            return raw
    if last_error is not None:
        LOGGER.debug("Tool schema convert failed (%s); using voluptuous fallback", last_error)
    return None


def _resolve_local_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline local $ref / $defs so required properties are not stripped to empty."""
    defs = schema.get("$defs") or schema.get("definitions") or {}
    if not isinstance(defs, dict):
        defs = {}

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(item) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            name = ref.rsplit("/", 1)[-1]
            target = defs.get(name)
            if isinstance(target, dict):
                merged = {**deepcopy(target), **{k: v for k, v in node.items() if k != "$ref"}}
                return resolve(merged)
        return {key: resolve(value) for key, value in node.items()}

    return resolve(schema)


def _object_branches(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect object variants from a root anyOf/oneOf/allOf union."""
    for key in ("anyOf", "oneOf", "allOf"):
        branches = schema.get(key)
        if not isinstance(branches, list):
            continue
        objects: list[dict[str, Any]] = []
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            nested = _object_branches(branch)
            if nested:
                objects.extend(nested)
            elif branch.get("type") == "object" or "properties" in branch:
                objects.append(branch)
        if objects:
            return objects
    if schema.get("type") == "object" or "properties" in schema:
        return [schema]
    return []


def _flatten_object_union(schema: dict[str, Any]) -> dict[str, Any]:
    """Merge a root union of objects into one object so required fields survive."""
    if schema.get("type") == "object" and isinstance(schema.get("properties"), dict):
        return schema
    objects = _object_branches(schema)
    if not objects:
        return schema
    if len(objects) == 1 and objects[0] is schema:
        return schema
    properties: dict[str, Any] = {}
    required_sets: list[set[str]] = []
    for obj in objects:
        for name, prop in (obj.get("properties") or {}).items():
            if isinstance(name, str) and name not in properties:
                properties[name] = prop
        required = obj.get("required")
        if isinstance(required, list):
            required_sets.append({item for item in required if isinstance(item, str)})
    merged: dict[str, Any] = {"type": "object", "properties": properties}
    if required_sets:
        required = set(required_sets[0])
        for extra in required_sets[1:]:
            required &= extra
        merged_required = [name for name in properties if name in required]
        if merged_required:
            merged["required"] = merged_required
    description = schema.get("description")
    if description:
        merged["description"] = description
    return merged


def _sanitize_property(prop: Any) -> dict[str, Any]:
    """Simplify one property to an xAI-safe typed schema."""
    if not isinstance(prop, dict):
        return {"type": "string"}
    prop = _flatten_property_union(prop)
    for key in _UNSUPPORTED_SCHEMA_KEYS:
        prop.pop(key, None)
    if "type" not in prop:
        if "properties" in prop:
            prop["type"] = "object"
        elif "items" in prop:
            prop["type"] = "array"
        else:
            prop["type"] = "string"
    if prop.get("type") == "array" and not isinstance(prop.get("items"), dict):
        prop["items"] = {"type": "string"}
    if prop.get("type") == "object":
        nested = prop.setdefault("properties", {})
        if isinstance(nested, dict):
            for name, child in list(nested.items()):
                nested[name] = _sanitize_property(child)
        else:
            prop["properties"] = {}
    return prop


def _flatten_property_union(prop: dict[str, Any]) -> dict[str, Any]:
    """Pick a concrete type from anyOf/oneOf, keeping description and default."""
    branches = prop.get("anyOf") or prop.get("oneOf")
    if not isinstance(branches, list):
        return prop
    typed = [
        branch
        for branch in branches
        if isinstance(branch, dict) and branch.get("type") not in (None, "null")
    ]
    if not typed:
        return prop
    chosen = deepcopy(typed[0])
    if prop.get("description") and not chosen.get("description"):
        chosen["description"] = prop["description"]
    if "default" in prop and "default" not in chosen:
        chosen["default"] = prop["default"]
    return chosen


def _type_from_voluptuous_value(value: Any) -> dict[str, Any]:
    """Infer a JSON Schema type from a voluptuous validator."""
    if value in _TYPE_NAMES:
        return {"type": _TYPE_NAMES[value]}
    inner = getattr(value, "type", None)
    if inner in _TYPE_NAMES:
        return {"type": _TYPE_NAMES[inner]}
    validators = getattr(value, "validators", None)
    if isinstance(validators, (list, tuple)):
        for validator in validators:
            if validator is None:
                continue
            if validator in _TYPE_NAMES:
                return {"type": _TYPE_NAMES[validator]}
            inferred = _type_from_voluptuous_value(validator)
            if inferred.get("type") != "string" or validator in (str,):
                return inferred
    return {"type": "string"}
