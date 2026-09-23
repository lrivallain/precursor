"""Characterisation of ``POST /api/workspaces/{id}/chat/stream``.

Workspace chat is ephemeral (the client keeps the history), but its SSE stream is
a contract ``WorkspaceChat.tsx`` depends on: ``delta``, ``tool_calls``,
``tool_result``, ``mcp_auth_required``, ``system``, ``error``, ``done`` and
``suggestions``. These tests pin that contract so the stream can be moved onto
the shared turn engine without the SPA noticing.

The seams are deliberately ones a refactor can't move: the provider is injected
through the provider registry (``get_llm_provider`` resolves it at call time,
wherever it is imported), and MCP through the process-wide client manager.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.types import CallToolResult, TextContent

from precursor.backend.main import create_app
from precursor.backend.services import app_settings
from precursor.backend.services.llm.base import (
    ChatMessage,
    LLMError,
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
    MCPToolAuthRequired,
    MCPToolDef,
    get_mcp_client_manager,
)
from precursor.backend.services.suggestions import SUGGESTIONS_INSTRUCTION

_FAKE_PROVIDER = "test-workspace-fake"


class _Call:
    def __init__(self, call_id: str, name: str, arguments: str = "{}") -> None:
        self.id = call_id
        self.name = name
        self.arguments = arguments


class _ScriptedProvider:
    """Replays one scripted list of provider events per round.

    The last round repeats once the script runs out, which is how the round-cap
    test keeps the model asking for tools forever.
    """

    name = "scripted"

    def __init__(self, *rounds: list[ProviderEvent], fail: Exception | None = None) -> None:
        self.rounds = list(rounds)
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def stream_chat(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[str]:
        async for event in self.stream_chat_with_tools(
            model=model, messages=messages, tools=[], reasoning_effort=reasoning_effort
        ):
            if isinstance(event, TextDeltaEvent):
                yield event.content

    async def stream_chat_with_tools(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDef],
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        _ = reasoning_effort
        self.calls.append(
            {"model": model, "messages": list(messages), "tools": [t.name for t in tools]}
        )
        if self.fail is not None:
            raise self.fail
        for event in self.rounds[min(len(self.calls), len(self.rounds)) - 1]:
            yield event


class _Bundle(ActiveTools):
    """An MCP bundle whose tool calls are answered by ``handler``."""

    handler: Any = None

    async def call_tool(self, server: str, raw_name: str, args: dict[str, Any]) -> Any:
        return await self.handler(server, raw_name, args)


def _text_round(text: str) -> list[ProviderEvent]:
    return [
        TextDeltaEvent(content=text),
        UsageEvent(prompt_tokens=11, completion_tokens=7, total_tokens=18),
        TurnDoneEvent(finish_reason="stop"),
    ]


def _tool_round(*calls: _Call, text: str = "") -> list[ProviderEvent]:
    events: list[ProviderEvent] = [TextDeltaEvent(content=text)] if text else []
    return [
        *events,
        ToolCallsEvent(calls=list(calls)),
        UsageEvent(prompt_tokens=5, completion_tokens=3, total_tokens=8),
        TurnDoneEvent(finish_reason="tool_calls"),
    ]


def _file_result(slug: str, path: str, text: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content={"workspace_slug": slug, "path": path, "content": text},
    )


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
    unavailable: Sequence[tuple[str, str]] = (),
    advertised_from_cache: Sequence[str] = (),
) -> None:
    manager = get_mcp_client_manager()

    @asynccontextmanager
    async def acquired(
        names: list[str], *, github_token: str = "", advertise_cached: bool = False
    ) -> AsyncIterator[ActiveTools]:
        _ = names, github_token, advertise_cached
        bundle = _Bundle(manager=manager)
        bundle.handler = handler
        bundle.unavailable.extend(unavailable)
        bundle.advertised_from_cache.update(advertised_from_cache)
        for tool in tools:
            bundle.tools.append(tool)
            bundle.tool_to_server[tool.qualified_name] = (tool.server, tool.name)
        yield bundle

    async def _no_wait(timeout: float) -> None:
        _ = timeout

    monkeypatch.setattr(manager, "acquired", acquired)
    # A paused call re-polls instead of sitting out a real browser sign-in.
    monkeypatch.setattr(manager, "wait_for_auth", _no_wait)


def _events(body: str) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        name = None
        data: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:") :].strip())
        if name:
            out.append((name, json.loads("\n".join(data)) if data else {}))
    return out


def _workspace(client: TestClient, name: str = "Docs") -> dict[str, Any]:
    r = client.post("/api/workspaces", json={"name": name, "kind": "local"})
    assert r.status_code == 201
    return r.json()


def _stream(client: TestClient, ws_id: int, **payload: Any) -> list[tuple[str, dict[str, Any]]]:
    r = client.post(
        f"/api/workspaces/{ws_id}/chat/stream",
        json={"content": "hello", **payload},
        headers={"Accept": "text/event-stream"},
    )
    assert r.status_code == 200
    return _events(r.text)


_READ = MCPToolDef(
    server="workspace-fs",
    name="read_file",
    description="Read a workspace file",
    input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
)


def test_text_only_turn_streams_deltas_then_done_and_suggestions(monkeypatch) -> None:
    reply = "Here is a tighter intro.\n\n```suggest\n- Shorten it more\n- Add an example\n```"
    provider = _ScriptedProvider(_text_round(reply))
    _install_provider(monkeypatch, provider)
    _install_mcp(monkeypatch)

    with TestClient(create_app()) as client:
        ws = _workspace(client)
        events = _stream(client, ws["id"])

    names = [n for n, _ in events]
    assert set(names) == {"delta", "done", "suggestions"}
    assert names[-2:] == ["done", "suggestions"]
    streamed = "".join(d["content"] for n, d in events if n == "delta")
    assert streamed == reply
    done = next(d for n, d in events if n == "done")
    # The suggestion block is lifted out of the final text...
    assert done["content"] == "Here is a tighter intro."
    # ...and surfaced as chips. No message id: workspace chat isn't persisted.
    assert next(d for n, d in events if n == "suggestions") == {
        "items": ["Shorten it more", "Add an example"]
    }


def test_prompt_carries_file_context_history_and_prompt_override(monkeypatch) -> None:
    provider = _ScriptedProvider(_text_round("ok"))
    _install_provider(monkeypatch, provider)
    _install_mcp(monkeypatch)

    with TestClient(create_app()) as client:
        ws = _workspace(client, "Prompted")
        created = client.post(
            f"/api/workspaces/{ws['id']}/file",
            json={"path": "intro.md", "content": "# Intro\n\nDraft body."},
        )
        assert created.status_code == 201
        _stream(
            client,
            ws["id"],
            content="/rewrite this",
            path="intro.md",
            model="fake-model",
            prompt_override="Rewrite the intro, please.",
            history=[
                {"role": "user", "content": "earlier question"},
                {"role": "assistant", "content": "earlier answer"},
            ],
        )

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["model"] == "fake-model"
    system, *rest = call["messages"]
    assert system.role == "system"
    assert "Draft body." in system.content
    assert "`intro.md`" in system.content
    assert f"workspace_id: {ws['id']}" in system.content
    assert SUGGESTIONS_INSTRUCTION in system.content
    # History is relayed as-is, then the model sees the expanded skill prompt
    # rather than the literal slash command the UI displays.
    assert [(m.role, m.content) for m in rest] == [
        ("user", "earlier question"),
        ("assistant", "earlier answer"),
        ("user", "Rewrite the intro, please."),
    ]


def test_one_tool_round_emits_tool_calls_and_tool_result(monkeypatch) -> None:
    provider = _ScriptedProvider(
        _tool_round(
            _Call("call-1", "workspace-fs__read_file", '{"path": "intro.md"}'),
            text="Let me look.",
        ),
        _text_round("It reads fine."),
    )
    _install_provider(monkeypatch, provider)
    seen: list[tuple[str, str, dict[str, Any]]] = []
    slug: dict[str, str] = {}

    async def handler(server: str, raw_name: str, args: dict[str, Any]) -> Any:
        seen.append((server, raw_name, args))
        return _file_result(slug["value"], "intro.md", "# Intro")

    _install_mcp(monkeypatch, tools=[_READ], handler=handler)

    with TestClient(create_app()) as client:
        ws = _workspace(client, "Tooled")
        slug["value"] = ws["slug"]
        events = _stream(client, ws["id"])

    names = [n for n, _ in events]
    assert names == ["delta", "tool_calls", "tool_result", "delta", "done"]
    assert seen == [("workspace-fs", "read_file", {"path": "intro.md"})]
    assert events[1][1] == {
        "calls": [
            {"id": "call-1", "name": "workspace-fs__read_file", "arguments": '{"path": "intro.md"}'}
        ]
    }
    result = events[2][1]
    assert result["tool_call_id"] == "call-1"
    assert result["name"] == "workspace-fs__read_file"
    assert result["arguments"] == '{"path": "intro.md"}'
    assert result["content"] == "# Intro"
    assert result["is_error"] is False
    assert events[-1][1]["content"] == "It reads fine."

    # The tool was advertised, and its result fed back for the second round.
    assert provider.calls[0]["tools"] == ["workspace-fs__read_file"]
    second = provider.calls[1]["messages"]
    assert second[-2].role == "assistant"
    assert second[-2].tool_calls[0]["id"] == "call-1"
    assert (second[-1].role, second[-1].tool_call_id, second[-1].content) == (
        "tool",
        "call-1",
        "# Intro",
    )


def test_auth_required_tool_prompts_then_retries_after_sign_in(monkeypatch) -> None:
    provider = _ScriptedProvider(
        _tool_round(_Call("call-1", "workspace-fs__read_file")),
        _text_round("Signed in and read it."),
    )
    _install_provider(monkeypatch, provider)
    attempts: list[int] = []

    async def handler(server: str, raw_name: str, args: dict[str, Any]) -> Any:
        _ = raw_name, args
        attempts.append(1)
        if len(attempts) == 1:
            raise MCPToolAuthRequired(server, "Sign-in expired.")
        return CallToolResult(content=[TextContent(type="text", text="body")])

    _install_mcp(monkeypatch, tools=[_READ], handler=handler)

    with TestClient(create_app()) as client:
        ws = _workspace(client, "Auth")
        events = _stream(client, ws["id"])

    names = [n for n, _ in events]
    assert names.index("tool_calls") < names.index("mcp_auth_required") < names.index("tool_result")
    assert next(d for n, d in events if n == "mcp_auth_required") == {
        "server": "workspace-fs",
        "message": "Sign-in expired.",
        "tool": "workspace-fs__read_file",
    }
    result = next(d for n, d in events if n == "tool_result")
    assert result["is_error"] is False
    assert result["content"] == "body"
    assert len(attempts) == 2
    assert names[-1] == "done"


def test_unavailable_server_is_reported_unless_advertised_from_cache(monkeypatch) -> None:
    _install_provider(monkeypatch, _ScriptedProvider(_text_round("fine")))
    _install_mcp(
        monkeypatch,
        unavailable=[("down", "connection refused"), ("cached", "needs sign-in")],
        advertised_from_cache=["cached"],
    )

    with TestClient(create_app()) as client:
        ws = _workspace(client, "Unavailable")
        events = _stream(client, ws["id"])

    systems = [d["message"] for n, d in events if n == "system"]
    assert systems == ["MCP server 'down' unavailable: connection refused"]
    assert events[-1][0] == "done"


def test_provider_failure_becomes_an_error_event(monkeypatch) -> None:
    _install_provider(monkeypatch, _ScriptedProvider(fail=LLMError("model not supported")))
    _install_mcp(monkeypatch)

    with TestClient(create_app()) as client:
        ws = _workspace(client, "Failing")
        events = _stream(client, ws["id"])

    assert events == [("error", {"message": "model not supported"})]


def test_tool_round_cap_ends_with_an_error_event(monkeypatch) -> None:
    provider = _ScriptedProvider(_tool_round(_Call("call-n", "workspace-fs__read_file")))
    _install_provider(monkeypatch, provider)

    async def handler(server: str, raw_name: str, args: dict[str, Any]) -> Any:
        _ = server, raw_name, args
        return CallToolResult(content=[TextContent(type="text", text="again")])

    _install_mcp(monkeypatch, tools=[_READ], handler=handler)

    with TestClient(create_app()) as client:
        ws = _workspace(client, "Capped")
        events = _stream(client, ws["id"])

    rounds = len(provider.calls)
    assert rounds >= 1
    assert [n for n, _ in events].count("tool_calls") == rounds
    assert events[-1] == ("error", {"message": f"Stopped after {rounds} tool rounds."})


# -- Drift fixed by running on the shared tool loop (#332) -------------------


def test_every_metered_round_is_counted_in_usage_stats(monkeypatch) -> None:
    provider = _ScriptedProvider(
        _tool_round(_Call("call-1", "workspace-fs__read_file")),
        _text_round("Counted."),
    )
    _install_provider(monkeypatch, provider)

    async def handler(server: str, raw_name: str, args: dict[str, Any]) -> Any:
        _ = server, raw_name, args
        return CallToolResult(content=[TextContent(type="text", text="body")])

    _install_mcp(monkeypatch, tools=[_READ], handler=handler)

    with TestClient(create_app()) as client:
        ws = _workspace(client, "Metered")
        before = client.get("/api/stats/usage").json()["totals"]
        events = _stream(client, ws["id"], model="fake-model")
        after = client.get("/api/stats/usage").json()["totals"]

    assert events[-1][0] == "done"
    # One ledger row per round: the tool round (5+3) and the answer (11+7).
    assert after["message_count"] - before["message_count"] == 2
    assert after["prompt_tokens"] - before["prompt_tokens"] == 16
    assert after["completion_tokens"] - before["completion_tokens"] == 10


async def test_usage_rows_are_tagged_as_workspace_traffic(monkeypatch) -> None:
    from sqlalchemy import func, select

    from precursor.backend.db import SessionLocal, init_db
    from precursor.backend.models import UsageRecord
    from precursor.backend.services.conversation_turn import TurnSettings
    from precursor.backend.services.workspace_chat import run_workspace_stream

    _install_mcp(monkeypatch)
    await init_db()
    async with SessionLocal() as session:
        last_id = (await session.execute(select(func.max(UsageRecord.id)))).scalar() or 0

    settings = TurnSettings(
        model="fake-model",
        reasoning_effort="",
        max_tool_rounds=3,
        max_input_tokens=100_000,
        max_tool_result_tokens=10_000,
        provider=_ScriptedProvider(_text_round("ok")),
        github_token="",
        enabled_servers=[],
    )
    _ = [
        ev
        async for ev in run_workspace_stream(
            system_prompt="sys", history=[ChatMessage(role="user", content="hi")], settings=settings
        )
    ]

    async with SessionLocal() as session:
        rows = (
            (await session.execute(select(UsageRecord).where(UsageRecord.id > last_id)))
            .scalars()
            .all()
        )
    assert [(r.source, r.model, r.topic_id, r.chat_id) for r in rows] == [
        ("workspace", "fake-model", None, None)
    ]


def test_tool_result_carries_the_workspace_file_link(monkeypatch) -> None:
    provider = _ScriptedProvider(
        _tool_round(_Call("call-1", "workspace-fs__read_file", '{"path": "intro.md"}')),
        _text_round("Linked."),
    )
    _install_provider(monkeypatch, provider)
    slug: dict[str, str] = {}

    async def handler(server: str, raw_name: str, args: dict[str, Any]) -> Any:
        _ = server, raw_name, args
        return _file_result(slug["value"], "intro.md", "# Intro")

    _install_mcp(monkeypatch, tools=[_READ], handler=handler)

    with TestClient(create_app()) as client:
        ws = _workspace(client, "Linked")
        slug["value"] = ws["slug"]
        events = _stream(client, ws["id"])

    result = next(d for n, d in events if n == "tool_result")
    # What WorkspaceChat.tsx turns into an Open chip — the other streams always
    # sent it; this one dropped it.
    assert result["link"] == {"slug": ws["slug"], "path": "intro.md"}


def test_tool_less_turn_is_trimmed_to_the_context_budget(monkeypatch) -> None:
    from precursor.backend.services import conversation_turn

    provider = _ScriptedProvider(_text_round("short"))
    _install_provider(monkeypatch, provider)
    _install_mcp(monkeypatch)

    async def _tiny_budget(_session: object) -> int:
        return 1

    monkeypatch.setattr(conversation_turn, "resolve_llm_max_input_tokens", _tiny_budget)

    with TestClient(create_app()) as client:
        ws = _workspace(client, "Budget")
        _stream(
            client,
            ws["id"],
            content="latest question",
            history=[
                {"role": "user" if i % 2 == 0 else "assistant", "content": f"old turn {i} " * 50}
                for i in range(10)
            ],
        )

    # The budget keeps only the newest turn next to the system prompt; before,
    # a turn with no tools enabled sent the whole history regardless.
    sent = provider.calls[0]["messages"]
    assert [(m.role, m.content) for m in sent[1:]] == [("user", "latest question")]
