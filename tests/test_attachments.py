"""Image attachments are read once per request, off the event loop."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

from custom_components.supergrok.client import ChatResult
from custom_components.supergrok.helpers import async_run_chat_log


def _jpeg(path: Path) -> Path:
    path.write_bytes(b"\xff\xd8\xff\xd9")
    return path


def _chat_log(hass: HomeAssistant, *contents: conversation.UserContent) -> conversation.ChatLog:
    chat_log = conversation.ChatLog(hass, "conv-1")
    for content in contents:
        chat_log.async_add_user_content(content)
    return chat_log


def _attachment(path: Path, mime_type: str = "image/jpeg") -> conversation.Attachment:
    return conversation.Attachment(
        media_content_id=f"media-source://camera/{path.name}",
        mime_type=mime_type,
        path=path,
    )


def _client(hass: HomeAssistant, chat) -> MagicMock:
    client = MagicMock()
    client.hass = hass
    client.chat_stream = MagicMock(side_effect=HomeAssistantError("stream down"))
    client.chat = chat
    return client


async def test_attachments_are_read_once_off_the_loop(
    hass: HomeAssistant, tmp_path: Path, monkeypatch
) -> None:
    """A tool loop reuses the data URL and does not read the file again."""
    image = _jpeg(tmp_path / "snap.jpg")
    reads = {"n": 0, "thread": None}
    real_read = Path.read_bytes

    def counting_read(self: Path) -> bytes:
        if self == image:
            reads["n"] += 1
            reads["thread"] = threading.current_thread()
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read)
    jobs = {"n": 0}
    real_job = hass.async_add_executor_job

    async def spy(func, *args, **kwargs):
        jobs["n"] += 1
        return await real_job(func, *args, **kwargs)

    monkeypatch.setattr(hass, "async_add_executor_job", spy)

    chat_log = _chat_log(
        hass,
        conversation.UserContent(content="What is this?", attachments=[_attachment(image)]),
        conversation.UserContent(content="And this?", attachments=[_attachment(image)]),
    )
    api = MagicMock()
    api.tools = []
    api.custom_serializer = None
    api.async_call_tool = AsyncMock(return_value=llm.ToolResult(data={"done": True}))
    chat_log.llm_api = api

    sent: list[list[dict]] = []

    async def chat(**kwargs):
        sent.append(kwargs["messages"])
        if len(sent) == 1:
            return ChatResult(
                text="",
                tool_calls=[{"id": "call_1", "name": "todo", "arguments": {}}],
            )
        return ChatResult(text="A doorway.")

    await async_run_chat_log(
        client=_client(hass, chat),
        chat_log=chat_log,
        model="latest",
        agent_id="ai_task.test",
    )

    def urls(messages: list[dict]) -> list[str]:
        found: list[str] = []
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                url = (part.get("image_url") or {}).get("url")
                if isinstance(url, str):
                    found.append(url)
        return found

    assert len(sent) == 2
    assert reads["n"] == 1
    assert jobs["n"] == 1
    assert reads["thread"] is not threading.current_thread()
    first = urls(sent[0])
    assert first == urls(sent[1])
    assert len(first) == 2
    assert first[0].startswith("data:image/jpeg;base64,")
    assert first[0] == first[1]


async def test_text_only_chat_does_not_read_files(
    hass: HomeAssistant, monkeypatch
) -> None:
    """A chat with no attachments never schedules an executor job."""
    jobs = {"n": 0}
    real_job = hass.async_add_executor_job

    async def spy(func, *args, **kwargs):
        jobs["n"] += 1
        return await real_job(func, *args, **kwargs)

    monkeypatch.setattr(hass, "async_add_executor_job", spy)
    chat_log = _chat_log(hass, conversation.UserContent(content="Hello"))

    async def chat(**_kwargs):
        return ChatResult(text="Hi.")

    await async_run_chat_log(
        client=_client(hass, chat),
        chat_log=chat_log,
        model="latest",
        agent_id="conversation.test",
    )
    assert jobs["n"] == 0


async def test_missing_attachment_errors_before_chat(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """A missing image fails in the executor and the model is not called."""
    missing = tmp_path / "gone.jpg"
    chat_log = _chat_log(
        hass,
        conversation.UserContent(content="Look", attachments=[_attachment(missing)]),
    )
    chat = AsyncMock()
    with pytest.raises(HomeAssistantError, match="does not exist"):
        await async_run_chat_log(
            client=_client(hass, chat),
            chat_log=chat_log,
            model="latest",
            agent_id="ai_task.test",
        )
    chat.assert_not_called()


async def test_non_image_attachment_errors(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Only image attachments are sent."""
    notes = tmp_path / "notes.txt"
    notes.write_text("not an image", encoding="utf-8")
    chat_log = _chat_log(
        hass,
        conversation.UserContent(
            content="Read this",
            attachments=[_attachment(notes, "text/plain")],
        ),
    )
    chat = AsyncMock()
    with pytest.raises(HomeAssistantError, match="Only image attachments"):
        await async_run_chat_log(
            client=_client(hass, chat),
            chat_log=chat_log,
            model="latest",
            agent_id="ai_task.test",
        )
    chat.assert_not_called()
