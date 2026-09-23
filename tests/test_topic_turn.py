"""Characterisation of the unattended topic turn (``services/turn.run_topic_turn``).

Scheduled prompts and the MCP ``post_message`` tool drive a turn without an SSE
client. These pin what that turn persists and which deliberate policies it
applies (the literal prompt is stored while the expanded one is sent, and
Precursor's own MCP server is never offered to itself), so its preparation can
be shared with the streaming routers without drifting.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from mcp.types import CallToolResult, TextContent
from sqlalchemy import select

from precursor.backend.db import SessionLocal, init_db
from precursor.backend.models import AppSetting, Message, MessageRole, Topic, UsageRecord
from precursor.backend.services import app_settings
from precursor.backend.services.llm.base import (
    ChatMessage,
    ProviderEvent,
    TextDeltaEvent,
    ToolCallsEvent,
    ToolDef,
    TurnDoneEvent,
    UsageEvent,
)
from precursor.backend.services.llm.registry import PROVIDERS, ProviderSpec
from precursor.backend.services.mcp.client import (
    ActiveTools,
    MCPToolDef,
    get_mcp_client_manager,
)
from precursor.backend.services.turn import run_topic_turn

_FAKE_PROVIDER = "test-topic-turn-fake"


class _Call:
    def __init__(self, call_id: str, name: str, arguments: str = "{}") -> None:
        self.id = call_id
        self.name = name
        self.arguments = arguments


class _ScriptedProvider:
    name = "scripted"

    def __init__(self, *rounds: list[ProviderEvent]) -> None:
        self.rounds = list(rounds)
        self.calls: list[list[ChatMessage]] = []

    async def stream_chat_with_tools(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDef],
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        _ = model, tools, reasoning_effort
        self.calls.append(list(messages))
        for event in self.rounds[min(len(self.calls), len(self.rounds)) - 1]:
            yield event

    async def list_models(self) -> list[SimpleNamespace]:
        # A one-model catalogue, so the turn resolves a known model id.
        return [SimpleNamespace(id="fake-model")]


class _Bundle(ActiveTools):
    handler: Any = None

    async def call_tool(self, server: str, raw_name: str, args: dict[str, Any]) -> Any:
        return await self.handler(server, raw_name, args)


def _install_provider(monkeypatch: pytest.MonkeyPatch, provider: _ScriptedProvider) -> None:
    monkeypatch.setitem(
        PROVIDERS,
        _FAKE_PROVIDER,
        ProviderSpec(id=_FAKE_PROVIDER, label="Fake", build=lambda _cfg, _tok: provider),
    )

    async def _resolve(_session: object) -> str:
        return _FAKE_PROVIDER

    monkeypatch.setattr(app_settings, "resolve_llm_provider", _resolve)


def _install_mcp(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tools: Sequence[MCPToolDef] = (),
    handler: Any = None,
) -> list[list[str]]:
    """Fake the MCP manager; returns the server lists each turn acquired."""
    manager = get_mcp_client_manager()
    requested: list[list[str]] = []

    @asynccontextmanager
    async def acquired(
        names: list[str], *, github_token: str = "", advertise_cached: bool = False
    ) -> AsyncIterator[ActiveTools]:
        _ = github_token, advertise_cached
        requested.append(list(names))
        bundle = _Bundle(manager=manager)
        bundle.handler = handler
        for tool in tools:
            bundle.tools.append(tool)
            bundle.tool_to_server[tool.qualified_name] = (tool.server, tool.name)
        yield bundle

    monkeypatch.setattr(manager, "acquired", acquired)
    return requested


@pytest.fixture
async def enabled_servers() -> AsyncIterator[None]:
    """Enable ``precursor`` + ``fetch`` for the turn, restoring the setting after."""
    await init_db()
    async with SessionLocal() as session:
        previous = await session.get(AppSetting, "mcp_enabled")
        saved = previous.value if previous is not None else None
        value = json.dumps({"precursor": True, "fetch": True, "off": False})
        if previous is None:
            session.add(AppSetting(key="mcp_enabled", value=value))
        else:
            previous.value = value
        await session.commit()
    try:
        yield
    finally:
        async with SessionLocal() as session:
            row = await session.get(AppSetting, "mcp_enabled")
            if saved is None:
                if row is not None:
                    await session.delete(row)
            elif row is not None:
                row.value = saved
            await session.commit()


async def _topic() -> int:
    await init_db()
    async with SessionLocal() as session:
        topic = Topic(title="Scheduled", slug=f"scheduled-turn-{uuid4().hex[:8]}")
        session.add(topic)
        await session.commit()
        await session.refresh(topic)
        return topic.id


async def _messages(topic_id: int) -> list[Message]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Message).where(Message.topic_id == topic_id).order_by(Message.id)
        )
        return list(result.scalars().all())


def _answer(text: str) -> list[ProviderEvent]:
    return [
        TextDeltaEvent(content=text),
        UsageEvent(prompt_tokens=13, completion_tokens=4, total_tokens=17),
        TurnDoneEvent(finish_reason="stop"),
    ]


async def test_persists_literal_prompt_but_sends_the_expanded_one(
    monkeypatch, enabled_servers
) -> None:
    provider = _ScriptedProvider(_answer("Bravo, in English."))
    _install_provider(monkeypatch, provider)
    requested = _install_mcp(monkeypatch)
    topic_id = await _topic()

    await run_topic_turn(topic_id, "/to-en bravo", llm_prompt="Translate to English: bravo")

    rows = await _messages(topic_id)
    assert [(m.role, m.content) for m in rows] == [
        (MessageRole.USER, "/to-en bravo"),
        (MessageRole.ASSISTANT, "Bravo, in English."),
    ]
    assert rows[1].prompt_tokens == 13
    assert rows[1].completion_tokens == 4
    system, *history = provider.calls[0]
    assert system.role == "system"
    assert "Topic title: Scheduled" in system.content
    assert [(m.role, m.content) for m in history] == [("user", "Translate to English: bravo")]
    # A programmatic turn never offers Precursor's own MCP server to itself.
    assert requested == [["fetch"]]


async def test_clear_context_starts_from_an_empty_transcript(monkeypatch, enabled_servers) -> None:
    provider = _ScriptedProvider(_answer("fresh"))
    _install_provider(monkeypatch, provider)
    _install_mcp(monkeypatch)
    topic_id = await _topic()
    async with SessionLocal() as session:
        session.add(Message(topic_id=topic_id, role=MessageRole.USER, content="old question"))
        session.add(Message(topic_id=topic_id, role=MessageRole.ASSISTANT, content="old answer"))
        await session.commit()

    await run_topic_turn(topic_id, "new question", clear_context=True)

    assert [m.content for m in await _messages(topic_id)] == ["new question", "fresh"]
    assert [m.content for m in provider.calls[0][1:]] == ["new question"]


async def test_history_is_replayed_to_the_model(monkeypatch, enabled_servers) -> None:
    provider = _ScriptedProvider(_answer("third"))
    _install_provider(monkeypatch, provider)
    _install_mcp(monkeypatch)
    topic_id = await _topic()
    async with SessionLocal() as session:
        session.add(Message(topic_id=topic_id, role=MessageRole.USER, content="first"))
        session.add(Message(topic_id=topic_id, role=MessageRole.ASSISTANT, content="second"))
        await session.commit()

    await run_topic_turn(topic_id, "next")

    assert [(m.role, m.content) for m in provider.calls[0][1:]] == [
        ("user", "first"),
        ("assistant", "second"),
        ("user", "next"),
    ]


async def test_tool_round_persists_the_call_and_its_result(monkeypatch, enabled_servers) -> None:
    provider = _ScriptedProvider(
        [
            ToolCallsEvent(calls=[_Call("call-1", "fetch__get", '{"url": "https://x"}')]),
            UsageEvent(prompt_tokens=5, completion_tokens=2, total_tokens=7),
            TurnDoneEvent(finish_reason="tool_calls"),
        ],
        _answer("Fetched."),
    )
    _install_provider(monkeypatch, provider)

    async def handler(server: str, raw_name: str, args: dict[str, Any]) -> Any:
        assert (server, raw_name, args) == ("fetch", "get", {"url": "https://x"})
        return CallToolResult(
            content=[TextContent(type="text", text="page body")],
            structured_content={"workspace_slug": "docs", "path": "a.md"},
        )

    _install_mcp(
        monkeypatch,
        tools=[
            MCPToolDef(
                server="fetch",
                name="get",
                description="GET a URL",
                input_schema={"type": "object", "properties": {}},
            )
        ],
        handler=handler,
    )
    topic_id = await _topic()

    await run_topic_turn(topic_id, "fetch it")

    rows = await _messages(topic_id)
    assert [m.role for m in rows] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    calls = json.loads(rows[1].tool_calls or "[]")
    assert calls[0]["id"] == "call-1"
    assert calls[0]["function"]["name"] == "fetch__get"
    assert rows[1].prompt_tokens == 5
    tool_meta = json.loads(rows[2].tool_calls or "{}")
    assert tool_meta == {
        "tool_call_id": "call-1",
        "name": "fetch__get",
        "arguments": '{"url": "https://x"}',
        "is_error": False,
        "link": {"slug": "docs", "path": "a.md"},
    }
    assert rows[2].content == "page body"
    assert rows[3].content == "Fetched."


async def test_missing_topic_is_a_no_op(monkeypatch) -> None:
    provider = _ScriptedProvider(_answer("never"))
    _install_provider(monkeypatch, provider)
    _install_mcp(monkeypatch)
    await init_db()

    await run_topic_turn(987_654, "hello?")

    assert provider.calls == []
    assert await _messages(987_654) == []


# -- Stored like a streamed turn (#332) --------------------------------------


async def _usage(topic_id: int) -> list[UsageRecord]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(UsageRecord).where(UsageRecord.topic_id == topic_id).order_by(UsageRecord.id)
        )
        return list(result.scalars().all())


async def test_answer_is_stored_like_a_streamed_one(monkeypatch, enabled_servers) -> None:
    reply = "Done.\n\n```suggest\n- Run it again\n- Show the log\n```"
    _install_provider(monkeypatch, _ScriptedProvider(_answer(reply)))
    _install_mcp(monkeypatch)
    topic_id = await _topic()

    await run_topic_turn(topic_id, "go")

    answer = (await _messages(topic_id))[-1]
    # The follow-up block becomes chips instead of leaking into the transcript.
    assert answer.content == "Done."
    assert json.loads(answer.suggestions or "[]") == ["Run it again", "Show the log"]
    assert answer.model == "fake-model"
    assert answer.elapsed_ms is not None and answer.elapsed_ms >= 0


async def test_every_metered_round_reaches_the_usage_ledger(monkeypatch, enabled_servers) -> None:
    provider = _ScriptedProvider(
        [
            ToolCallsEvent(calls=[_Call("call-1", "fetch__get")]),
            UsageEvent(prompt_tokens=5, completion_tokens=2, total_tokens=7),
            TurnDoneEvent(finish_reason="tool_calls"),
        ],
        _answer("Fetched."),
    )
    _install_provider(monkeypatch, provider)

    async def handler(server: str, raw_name: str, args: dict[str, Any]) -> Any:
        _ = server, raw_name, args
        return CallToolResult(content=[TextContent(type="text", text="ok")])

    _install_mcp(
        monkeypatch,
        tools=[
            MCPToolDef(
                server="fetch",
                name="get",
                description="GET a URL",
                input_schema={"type": "object", "properties": {}},
            )
        ],
        handler=handler,
    )
    topic_id = await _topic()

    await run_topic_turn(topic_id, "fetch it")

    usage = await _usage(topic_id)
    assert [(u.prompt_tokens, u.completion_tokens, u.total_tokens) for u in usage] == [
        (5, 2, 7),
        (13, 4, 17),
    ]
    assert {(u.source, u.model) for u in usage} == {("chat", "fake-model")}
