"""SSE-level coverage for the deferred MCP sign-in prompt.

`tests/test_mcp_deferred_auth.py` covers the policy at the `run_tool_loop`
layer. This covers the *transport* contract that the SPA actually consumes:
`run_message_stream` must turn a `ToolAuthRequired` into an `mcp_auth_required`
SSE event carrying `server`, `message` and the `tool` that needs the sign-in,
and must not stall the turn before the model has run.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from precursor.backend.db import SessionLocal, init_db
from precursor.backend.models import Topic
from precursor.backend.services import turn_engine as turn_engine_mod
from precursor.backend.services.llm.base import (
    TextDeltaEvent,
    ToolCallsEvent,
    TurnDoneEvent,
)
from precursor.backend.services.mcp.client import (
    ActiveTools,
    MCPClientManager,
    MCPServerEntry,
    MCPToolAuthRequired,
    MCPToolDef,
)


class _Call:
    def __init__(self, name: str) -> None:
        self.id = "call-1"
        self.name = name
        self.arguments = "{}"


class _CallsThenAnswers:
    name = "fake"

    def __init__(self) -> None:
        self.rounds = 0
        self.advertised: list[list[str]] = []

    async def stream_chat_with_tools(self, *, model, messages, tools, reasoning_effort=None):  # type: ignore[no-untyped-def]
        _ = model, messages, reasoning_effort
        self.advertised.append([t.name for t in tools])
        self.rounds += 1
        if self.rounds == 1:
            yield ToolCallsEvent(calls=[_Call("workiq__search")])
        else:
            yield TextDeltaEvent(content="answered without the tool")
        yield TurnDoneEvent(finish_reason="stop")


class _BlockedBundle(ActiveTools):
    """Bundle whose only server always demands a sign-in at call time."""

    async def call_tool(self, server: str, raw_name: str, args: dict[str, Any]) -> Any:
        _ = raw_name, args
        raise MCPToolAuthRequired(server, "WorkIQ sign-in expired.")


async def _topic_id() -> int:
    await init_db()
    async with SessionLocal() as session:
        topic = Topic(title="deferred auth sse", slug=f"deferred-auth-sse-{uuid4().hex[:8]}")
        session.add(topic)
        await session.commit()
        await session.refresh(topic)
        return topic.id


def _install_manager(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Point the stream at a manager that hands back a permanently blocked bundle."""
    manager = MCPClientManager()
    entry = MCPServerEntry(name="workiq", transport="streamable_http", url="https://x")
    entry.state = "needs_auth"
    entry.tools = [
        MCPToolDef(
            server="workiq",
            name="search",
            description="search mail",
            input_schema={"type": "object", "properties": {}},
        )
    ]
    manager._servers = {"workiq": entry}

    class _Ctx:
        async def __aenter__(self) -> ActiveTools:
            bundle = _BlockedBundle(manager=manager)
            bundle.advertised_from_cache.add("workiq")
            bundle.unavailable.append(("workiq", "Sign-in required."))
            for tool in entry.tools:
                bundle.tools.append(tool)
                bundle.tool_to_server[tool.qualified_name] = ("workiq", tool.name)
            return bundle

        async def __aexit__(self, *exc: object) -> bool:
            return False

    def acquired(names: list[str], *, github_token: str = "", advertise_cached: bool = False):  # type: ignore[no-untyped-def]
        _ = names, github_token, advertise_cached
        return _Ctx()

    manager.acquired = acquired  # type: ignore[assignment]
    monkeypatch.setattr(turn_engine_mod, "get_mcp_client_manager", lambda: manager)

    async def _no_usage(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(turn_engine_mod, "record_usage", _no_usage)


async def _run(topic_id: int, provider: _CallsThenAnswers) -> list[dict[str, str]]:
    return [
        ev
        async for ev in turn_engine_mod.run_message_stream(
            kind="topic",
            container_id=topic_id,
            system_prompt="sys",
            history=[],
            user_echo={"id": 1, "content": "hi"},
            model="m",
            reasoning_effort="low",
            max_tool_rounds=3,
            max_input_tokens=10_000,
            max_tool_result_tokens=1_000,
            provider=provider,
            github_token="",
            enabled_servers=["workiq"],
            # Bound the interactive window: these assert the transport contract,
            # not how long a real user gets to finish a browser sign-in.
            auth_wait_timeout=0.05,
        )
    ]


async def test_stream_emits_mcp_auth_required_naming_the_tool(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The user-visible contract for the whole feature."""
    _install_manager(monkeypatch)
    provider = _CallsThenAnswers()
    events = await _run(await _topic_id(), provider)

    prompts = [e for e in events if e["event"] == "mcp_auth_required"]
    assert len(prompts) == 1
    payload = json.loads(prompts[0]["data"])
    assert payload["server"] == "workiq"
    assert payload["message"] == "WorkIQ sign-in expired."
    # The field the old turn-start prompt could not carry.
    assert payload["tool"] == "workiq__search"


async def test_stream_starts_the_turn_instead_of_gating_on_the_sign_in(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The prompt arrives *after* the model ran, not before it."""
    _install_manager(monkeypatch)
    provider = _CallsThenAnswers()
    events = await _run(await _topic_id(), provider)

    kinds = [e["event"] for e in events]
    assert kinds[0] == "user_message"
    # The model was called and asked for the tool before any sign-in prompt.
    assert kinds.index("tool_calls") < kinds.index("mcp_auth_required")
    # The blocked server's tools were still advertised, so the model reached for
    # the tool rather than answering from memory.
    assert provider.advertised[0] == ["workiq__search"]


async def test_stream_reports_a_tool_error_when_the_sign_in_never_lands(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_manager(monkeypatch)
    events = await _run(await _topic_id(), _CallsThenAnswers())

    results = [e for e in events if e["event"] == "tool_result"]
    assert len(results) == 1
    payload = json.loads(results[0]["data"])
    assert payload["is_error"] is True
    assert "sign-in" in payload["content"].lower()
    # And no "unavailable" noise for a server whose tools were on offer.
    systems = [json.loads(e["data"])["message"] for e in events if e["event"] == "system"]
    assert not any("unavailable" in m for m in systems)
