"""Shared chat-log translation helpers."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.components import conversation
from homeassistant.helpers import llm
from homeassistant.helpers.json import json_dumps
from voluptuous_openapi import convert

from homeassistant.exceptions import HomeAssistantError

from .const import LOGGER, MAX_TOOL_ITERATIONS, REALTIME_ENABLED
from .client import ChatResult, GrokClient
from .logutil import summarize_tools


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


def _sanitize_tool_schema(schema: Any) -> dict[str, Any]:
    """xAI requires the parameters root to be a JSON object (not array/scalar/anyOf)."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    for key in _UNSUPPORTED_SCHEMA_KEYS:
        schema.pop(key, None)

    schema_type = schema.get("type")
    if schema_type != "object":
        # voluptuous_openapi sometimes emits a bare type or an array at the root.
        if "properties" not in schema:
            return {"type": "object", "properties": {}}
        schema["type"] = "object"

    properties = schema.setdefault("properties", {})
    if not isinstance(properties, dict):
        schema["properties"] = {}
        properties = schema["properties"]

    for name, prop in list(properties.items()):
        if not isinstance(prop, dict):
            properties[name] = {"type": "string"}
            continue
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
            prop.setdefault("properties", {})

    if "required" in schema and not isinstance(schema["required"], list):
        schema.pop("required")
    return schema


def format_tools(llm_api: llm.APIInstance) -> list[dict[str, Any]]:
    """Convert Home Assistant LLM tools to xAI-safe function specs."""
    tools: list[dict[str, Any]] = []
    for tool in llm_api.tools:
        try:
            raw = convert(tool.parameters, custom_serializer=llm_api.custom_serializer)
        except Exception as err:  # noqa: BLE001
            LOGGER.debug("Tool %s schema convert failed (%s); using empty object", tool.name, err)
            raw = {}
        if isinstance(raw, dict) and raw.get("type") not in (None, "object") and "properties" not in raw:
            LOGGER.debug("Tool %s had root type=%s; wrapping as object", tool.name, raw.get("type"))
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": _sanitize_tool_schema(raw),
                },
            }
        )
    return tools


def chat_log_to_messages(chat_log: conversation.ChatLog) -> list[dict[str, Any]]:
    """Convert a Home Assistant chat log to chat-completion messages."""
    messages: list[dict[str, Any]] = []
    pending_tool_calls: list[dict[str, Any]] = []

    for content in chat_log.content:
        if isinstance(content, conversation.ToolResultContent):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": content.tool_call_id,
                    "name": content.tool_name,
                    "content": json_dumps(content.tool_result),
                }
            )
            continue

        if isinstance(content, conversation.AssistantContent):
            message: dict[str, Any] = {"role": "assistant", "content": content.content or ""}
            if content.tool_calls:
                message["tool_calls"] = [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.tool_name,
                            "arguments": json_dumps(tool_call.tool_args),
                        },
                    }
                    for tool_call in content.tool_calls
                ]
                pending_tool_calls = message["tool_calls"]
            messages.append(message)
            continue

        if content.content:
            role = "system" if content.role == "system" else content.role
            messages.append({"role": role, "content": content.content})

    if pending_tool_calls:
        # Keep the type checker quiet; unused besides documenting pairing.
        _ = pending_tool_calls
    return messages


async def async_run_chat_log(
    *,
    client: GrokClient,
    chat_log: conversation.ChatLog,
    model: str,
    agent_id: str,
    max_tokens: int = 4096,
    realtime: bool = False,
    voice: str = "eve",
) -> None:
    """Drive a chat log to an assistant final answer, executing HA tools."""
    for iteration in range(MAX_TOOL_ITERATIONS):
        messages = chat_log_to_messages(chat_log)
        tools = format_tools(chat_log.llm_api) if chat_log.llm_api else None
        LOGGER.debug(
            "Turn %s/%s agent=%s model=%s realtime=%s messages=%s tools=%s",
            iteration + 1,
            MAX_TOOL_ITERATIONS,
            agent_id,
            model,
            realtime,
            len(messages),
            summarize_tools(tools),
        )
        if realtime and REALTIME_ENABLED:
            instructions = ""
            user_text = ""
            for message in messages:
                if message.get("role") == "system":
                    instructions = str(message.get("content") or "")
                elif message.get("role") == "user":
                    user_text = str(message.get("content") or "")
            try:
                result: ChatResult = await client.realtime_text(
                    model=model,
                    instructions=instructions,
                    user_text=user_text or (messages[-1].get("content") if messages else ""),
                    tools=tools,
                    voice=voice,
                )
            except HomeAssistantError as err:
                LOGGER.warning("Realtime failed (%s); falling back to chat completions", err)
                result = await client.chat(
                    model=model,
                    messages=messages,
                    tools=tools,
                    max_tokens=max_tokens,
                )
        else:
            result = await client.chat(
                model=model,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
            )

        if result.tool_calls and chat_log.llm_api:
            tool_inputs = [
                llm.ToolInput(
                    id=call["id"],
                    tool_name=call["name"],
                    tool_args=call["arguments"],
                )
                for call in result.tool_calls
            ]
            async for _ in chat_log.async_add_assistant_content(
                conversation.AssistantContent(
                    agent_id=agent_id,
                    content=result.text or None,
                    tool_calls=tool_inputs,
                )
            ):
                pass
            if not chat_log.unresponded_tool_results:
                break
            continue

        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=agent_id,
                content=result.text or " ",
            )
        )
        break
