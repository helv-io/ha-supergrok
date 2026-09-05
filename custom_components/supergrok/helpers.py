"""Shared chat-log translation helpers."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.helpers.json import json_dumps

from .client import ChatResult, GrokClient
from .const import LOGGER, MAX_TOOL_ITERATIONS, REALTIME_ENABLED
from .logutil import summarize_tools
from .toolschema import (
    convert_tool_parameters,
    missing_required_properties,
    sanitize_tool_schema,
)


def format_tools(llm_api: llm.APIInstance) -> list[dict[str, Any]]:
    """Convert Home Assistant LLM tools to xAI-safe function specs."""
    tools: list[dict[str, Any]] = []
    for tool in llm_api.tools:
        parameters = convert_tool_parameters(
            tool.parameters, llm_api.custom_serializer
        )
        if parameters.get("type") not in (None, "object") and "properties" not in parameters:
            LOGGER.debug("Tool %s had root type=%s; wrapping as object", tool.name, parameters.get("type"))
            parameters = sanitize_tool_schema(parameters)
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": parameters,
                },
            }
        )
    return tools


def _image_data_url(path: Path, mime_type: str | None) -> str:
    """Read an image file into a data URL. Caller must allow the path."""
    if not path.exists():
        raise HomeAssistantError(f"`{path}` does not exist")
    mime = mime_type or "image/jpeg"
    if not mime.startswith("image/"):
        raise HomeAssistantError(f"Only image attachments are supported (`{path}` is {mime})")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _user_content_with_attachments(content: conversation.UserContent) -> dict[str, Any]:
    """Build an OpenAI-style user message, including image attachments."""
    text = content.content or ""
    attachments = getattr(content, "attachments", None) or ()
    if not attachments:
        return {"role": "user", "content": text}
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    for attachment in attachments:
        path = Path(attachment.path)
        mime = getattr(attachment, "mime_type", None)
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": _image_data_url(path, mime)},
            }
        )
    if not parts:
        parts.append({"type": "text", "text": text or " "})
    return {"role": "user", "content": parts}


def chat_log_to_messages(chat_log: conversation.ChatLog) -> list[dict[str, Any]]:
    """Convert a Home Assistant chat log to chat-completion messages."""
    messages: list[dict[str, Any]] = []

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
            messages.append(message)
            continue

        if isinstance(content, conversation.UserContent):
            messages.append(_user_content_with_attachments(content))
            continue

        if content.content:
            role = "system" if content.role == "system" else content.role
            messages.append({"role": role, "content": content.content})

    return messages


def _tool_schemas_by_name(tools: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Map advertised function names to their parameter schemas."""
    schemas: dict[str, dict[str, Any]] = {}
    for tool in tools or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(function, dict) or not function.get("name"):
            continue
        parameters = function.get("parameters")
        schemas[str(function["name"])] = parameters if isinstance(parameters, dict) else {}
    return schemas


def _tool_input_from_call(
    call: dict[str, Any], schemas: dict[str, dict[str, Any]]
) -> tuple[llm.ToolInput, list[str]]:
    """Build a ToolInput and list required properties that are still missing."""
    args = call["arguments"] if isinstance(call.get("arguments"), dict) else {}
    missing = missing_required_properties(schemas.get(call["name"]), args)
    return (
        llm.ToolInput(
            id=call["id"],
            tool_name=call["name"],
            tool_args=args,
            external=bool(missing),
        ),
        missing,
    )


def _rejected_tool_result(missing: list[str]) -> dict[str, Any]:
    """Tool result that explains why SuperGrok did not dispatch the MCP call."""
    names = ", ".join(missing)
    return {
        "error": "missing_required_arguments",
        "error_text": (
            f"Tool call was not sent because required arguments were missing: {names}. "
            "Call the tool again with a JSON object that includes every required property."
        ),
        "missing": missing,
    }


def _add_rejected_tool_results(
    chat_log: conversation.ChatLog,
    agent_id: str,
    rejected: list[tuple[llm.ToolInput, list[str]]],
) -> None:
    """Record validation failures for tool calls that were not dispatched."""
    for tool_input, missing in rejected:
        LOGGER.warning(
            "Rejected empty or incomplete tool call %s missing %s",
            tool_input.tool_name,
            ",".join(missing),
        )
        chat_log.async_add_assistant_content_without_tools(
            conversation.ToolResultContent(
                agent_id=agent_id,
                tool_call_id=tool_input.id,
                tool_name=tool_input.tool_name,
                tool_result=_rejected_tool_result(missing),
            )
        )


async def _transform_stream(
    events: AsyncIterator[dict[str, Any]],
    schemas: dict[str, dict[str, Any]],
    rejected: list[tuple[llm.ToolInput, list[str]]],
) -> AsyncIterator[conversation.AssistantContentDeltaDict]:
    """Map Grok chat-completion stream events to HA deltas."""
    started = False
    async for event in events:
        if not started:
            yield {"role": "assistant"}
            started = True
        if content := event.get("content"):
            yield {"content": content}
        if event.get("tool_calls"):
            inputs: list[llm.ToolInput] = []
            for call in event["tool_calls"]:
                tool_input, missing = _tool_input_from_call(call, schemas)
                if missing:
                    rejected.append((tool_input, missing))
                inputs.append(tool_input)
            yield {"tool_calls": inputs}


async def async_run_chat_log(
    *,
    client: GrokClient,
    chat_log: conversation.ChatLog,
    model: str,
    agent_id: str,
    max_tokens: int = 4096,
    temperature: float | None = None,
    realtime: bool = False,
    voice: str = "eve",
    response_format: dict[str, Any] | None = None,
) -> None:
    """Drive a chat log to an assistant final answer, executing HA tools."""
    last_had_tools = False
    for iteration in range(MAX_TOOL_ITERATIONS):
        messages = chat_log_to_messages(chat_log)
        tools = format_tools(chat_log.llm_api) if chat_log.llm_api else None
        force_final = last_had_tools and iteration == MAX_TOOL_ITERATIONS - 1
        if force_final:
            tools = None
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
        if realtime and REALTIME_ENABLED and not force_final:
            instructions = ""
            user_text = ""
            for message in messages:
                if message.get("role") == "system":
                    instructions = str(message.get("content") or "")
                elif message.get("role") == "user":
                    content = message.get("content") or ""
                    user_text = content if isinstance(content, str) else ""
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
                    temperature=temperature,
                    response_format=response_format,
                )
            await _apply_chat_result(chat_log, agent_id, result, tools)
        else:
            try:
                stream = client.chat_stream(
                    model=model,
                    messages=messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    response_format=response_format,
                )
                rejected: list[tuple[llm.ToolInput, list[str]]] = []
                async for _ in chat_log.async_add_delta_content_stream(
                    agent_id,
                    _transform_stream(stream, _tool_schemas_by_name(tools), rejected),
                ):
                    pass
                _add_rejected_tool_results(chat_log, agent_id, rejected)
            except HomeAssistantError as err:
                LOGGER.warning("Chat stream failed (%s); falling back to non-stream", err)
                result = await client.chat(
                    model=model,
                    messages=messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    response_format=response_format,
                )
                await _apply_chat_result(chat_log, agent_id, result, tools)

        last_content = chat_log.content[-1] if chat_log.content else None
        last_had_tools = bool(
            isinstance(last_content, conversation.AssistantContent) and last_content.tool_calls
        )
        if not chat_log.unresponded_tool_results:
            if not isinstance(last_content, conversation.AssistantContent):
                chat_log.async_add_assistant_content_without_tools(
                    conversation.AssistantContent(agent_id=agent_id, content=" ")
                )
            break
    else:
        if not isinstance(chat_log.content[-1], conversation.AssistantContent) or (
            chat_log.content[-1].tool_calls and chat_log.unresponded_tool_results
        ):
            chat_log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(
                    agent_id=agent_id,
                    content="I reached the tool-call limit before finishing that request.",
                )
            )


async def _apply_chat_result(
    chat_log: conversation.ChatLog,
    agent_id: str,
    result: ChatResult,
    tools: list[dict[str, Any]] | None = None,
) -> None:
    """Apply a non-stream ChatResult onto the chat log."""
    if result.tool_calls and chat_log.llm_api:
        schemas = _tool_schemas_by_name(tools)
        tool_inputs: list[llm.ToolInput] = []
        rejected: list[tuple[llm.ToolInput, list[str]]] = []
        for call in result.tool_calls:
            tool_input, missing = _tool_input_from_call(call, schemas)
            if missing:
                rejected.append((tool_input, missing))
            tool_inputs.append(tool_input)
        async for _ in chat_log.async_add_assistant_content(
            conversation.AssistantContent(
                agent_id=agent_id,
                content=result.text or None,
                tool_calls=tool_inputs,
            )
        ):
            pass
        _add_rejected_tool_results(chat_log, agent_id, rejected)
        return
    chat_log.async_add_assistant_content_without_tools(
        conversation.AssistantContent(
            agent_id=agent_id,
            content=result.text or " ",
        )
    )
