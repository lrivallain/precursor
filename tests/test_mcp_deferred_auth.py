"""Tests for the deferred MCP auth gate.

The old gate ran *before* the LLM: if any enabled server was parked in
``needs_auth``, the whole turn blocked on ``wait_for_auth`` for up to five
minutes — so one stale WorkIQ token stalled a question that had nothing to do
with WorkIQ.

The gate now runs at *tool-call* time. What has to stay true:

* a blocked server still contributes its tools to the advertised list, so the
  model calls the tool instead of answering from memory (the hallucination risk
  the original gate existed to prevent);
* the turn itself starts immediately;
* the sign-in prompt still appears — at the moment a tool needs it, naming the
  tool — and the call is retried once after the user signs in;
* a sign-in that never happens yields a tool *error*, never a silent answer;
* an unattended run (the scheduler) fails fast instead of parking for minutes.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

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
from precursor.backend.services.turn_engine import (
    AssistantFinalTurn,
    ToolAuthRequired,
    ToolResultTurn,
    run_tool_loop,
)


def _patch_failing_transport(manager: MCPClientManager, exc: BaseException, *, before=None) -> None:  # type: ignore[no-untyped-def]
    """Make every connect on ``manager`` fail the way a dead credential does."""

    @asynccontextmanager
    async def fail(name: str, *, github_token: str = ""):  # type: ignore[no-untyped-def]
        _ = name, github_token
        if before is not None:
            before()
        raise exc
        yield  # pragma: no cover - keeps this an async generator

    manager._open_transport = fail  # type: ignore[assignment]


class _Call:
    def __init__(self, name: str) -> None:
        self.id = "call-1"
        self.name = name
        self.arguments = "{}"


class _OneToolProvider:
    """Calls ``tool_name`` on the first round, then answers on the second."""

    name = "fake"

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self.rounds = 0
        self.advertised: list[list[str]] = []

    async def stream_chat_with_tools(self, *, model, messages, tools, reasoning_effort=None):  # type: ignore[no-untyped-def]
        _ = model, messages, reasoning_effort
        self.advertised.append([t.name for t in tools])
        self.rounds += 1
        if self.rounds == 1:
            yield ToolCallsEvent(calls=[_Call(self.tool_name)])
        else:
            yield TextDeltaEvent(content="done")
        yield TurnDoneEvent(finish_reason="stop")


class _StubTools(ActiveTools):
    """``ActiveTools`` with a scripted ``call_tool``.

    ``ActiveTools`` is a slots dataclass, so a test can't patch the bound method
    on an instance; overriding in a subclass is the seam.
    """

    def __init__(self, manager: MCPClientManager, responder) -> None:  # type: ignore[no-untyped-def]
        super().__init__(manager=manager)
        self._responder = responder

    async def call_tool(self, server: str, raw_name: str, args: dict[str, Any]) -> Any:
        return await self._responder(server, raw_name, args)


def _blocked_bundle(
    manager: MCPClientManager, *, tools: list[MCPToolDef], responder
) -> ActiveTools:  # type: ignore[no-untyped-def]
    bundle = _StubTools(manager, responder)
    bundle.advertised_from_cache.add("workiq")
    bundle.unavailable.append(("workiq", "Sign-in required."))
    for tool in tools:
        bundle.tools.append(tool)
        bundle.tool_to_server[tool.qualified_name] = ("workiq", tool.name)
    return bundle


def _tool() -> MCPToolDef:
    return MCPToolDef(
        server="workiq",
        name="search",
        description="search mail",
        input_schema={"type": "object", "properties": {}},
    )


async def _drive(bundle: ActiveTools, provider: _OneToolProvider, **kwargs: Any) -> list[Any]:
    return [
        ev
        async for ev in run_tool_loop(
            active=bundle,
            provider=provider,
            model="m",
            reasoning_effort="low",
            system_prompt="sys",
            history=[],
            max_tool_rounds=3,
            max_input_tokens=10_000,
            max_tool_result_tokens=1_000,
            **kwargs,
        )
    ]


async def test_blocked_server_still_advertises_its_tools() -> None:
    """Dropping the tools would make the model answer from memory instead."""
    manager = MCPClientManager()
    entry = MCPServerEntry(name="workiq", transport="streamable_http", url="https://x")
    entry.state = "needs_auth"
    entry.error = "Sign-in required."
    entry.tools = [_tool()]
    manager._servers = {"workiq": entry}

    _patch_failing_transport(manager, RuntimeError("Sign-in required."))

    bundle = await manager.acquire(["workiq"], advertise_cached=True)
    assert [t.qualified_name for t in bundle.tools] == ["workiq__search"]
    assert bundle.tool_to_server["workiq__search"] == ("workiq", "search")
    assert "workiq" in bundle.advertised_from_cache
    # …and it is honest about not having a session for it.
    assert bundle.workers == {}
    assert [n for n, _ in bundle.unavailable] == ["workiq"]


async def test_cached_tools_are_not_advertised_without_the_opt_in() -> None:
    manager = MCPClientManager()
    entry = MCPServerEntry(name="workiq", transport="streamable_http", url="https://x")
    entry.tools = [_tool()]
    manager._servers = {"workiq": entry}

    _patch_failing_transport(manager, RuntimeError("nope"))

    bundle = await manager.acquire(["workiq"])
    assert bundle.tools == []


async def test_call_prompts_for_sign_in_and_retries_once() -> None:
    """The prompt fires at call time and names the tool; the retry then succeeds."""
    manager = MCPClientManager()
    state = {"signed_in": False}

    async def responder(server: str, raw_name: str, args: dict[str, Any]) -> Any:
        _ = args
        if not state["signed_in"]:
            raise MCPToolAuthRequired(server, "WorkIQ sign-in expired.")
        return {"content": [], "raw": raw_name}

    bundle = _blocked_bundle(manager, tools=[_tool()], responder=responder)

    async def sign_in_soon() -> None:
        await asyncio.sleep(0.05)
        state["signed_in"] = True
        manager.signal_auth_resolved()

    provider = _OneToolProvider("workiq__search")
    signer = asyncio.create_task(sign_in_soon())
    events = await _drive(bundle, provider, auth_wait_timeout=5.0)
    await signer

    prompts = [e for e in events if isinstance(e, ToolAuthRequired)]
    assert len(prompts) == 1
    assert prompts[0].server == "workiq"
    assert prompts[0].tool == "workiq__search"  # names what needs the sign-in

    results = [e for e in events if isinstance(e, ToolResultTurn)]
    assert len(results) == 1
    assert results[0].is_error is False
    assert any(isinstance(e, AssistantFinalTurn) for e in events)
    # The tools stayed on offer for the whole turn.
    assert provider.advertised[0] == ["workiq__search"]


async def test_sign_in_never_completed_yields_a_tool_error() -> None:
    """No silent answer: the model is told the tool could not run."""
    manager = MCPClientManager()

    async def responder(server: str, raw_name: str, args: dict[str, Any]) -> Any:
        _ = raw_name, args
        raise MCPToolAuthRequired(server, "WorkIQ sign-in expired.")

    bundle = _blocked_bundle(manager, tools=[_tool()], responder=responder)

    events = await _drive(bundle, _OneToolProvider("workiq__search"), auth_wait_timeout=0.05)

    assert len([e for e in events if isinstance(e, ToolAuthRequired)]) == 1
    results = [e for e in events if isinstance(e, ToolResultTurn)]
    assert len(results) == 1
    assert results[0].is_error is True
    assert "sign-in" in results[0].result_text.lower()
    assert "do not guess" in results[0].result_text.lower()


async def test_unattended_run_fails_fast_instead_of_waiting() -> None:
    """The scheduler has nobody to sign in; it must not park for the full window."""
    manager = MCPClientManager()

    async def responder(server: str, raw_name: str, args: dict[str, Any]) -> Any:
        _ = raw_name, args
        raise MCPToolAuthRequired(server, "WorkIQ sign-in expired.")

    bundle = _blocked_bundle(manager, tools=[_tool()], responder=responder)

    started = time.perf_counter()
    events = await _drive(bundle, _OneToolProvider("workiq__search"), auth_wait_timeout=0.0)
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0
    # Still visible: the prompt is published so the app-global banner appears.
    assert len([e for e in events if isinstance(e, ToolAuthRequired)]) == 1
    assert [e.is_error for e in events if isinstance(e, ToolResultTurn)] == [True]


async def test_turn_is_not_stalled_by_an_unrelated_blocked_server() -> None:
    """The headline behaviour: a stale credential no longer delays the answer."""
    manager = MCPClientManager()
    blocked = MCPServerEntry(name="workiq", transport="streamable_http", url="https://x")
    blocked.state = "needs_auth"
    blocked.tools = [_tool()]
    manager._servers = {"workiq": blocked}

    _patch_failing_transport(manager, RuntimeError("Sign-in required."))

    class _PlainProvider:
        name = "fake"

        async def stream_chat_with_tools(self, *, model, messages, tools, reasoning_effort=None):  # type: ignore[no-untyped-def]
            _ = model, messages, tools, reasoning_effort
            yield TextDeltaEvent(content="the answer")
            yield TurnDoneEvent(finish_reason="stop")

    started = time.perf_counter()
    bundle = await manager.acquire(["workiq"], advertise_cached=True)
    events = await _drive(bundle, _PlainProvider())  # type: ignore[arg-type]
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0  # not the 300s the pre-LLM gate would have cost
    assert [e.text for e in events if isinstance(e, AssistantFinalTurn)] == ["the answer"]


async def test_ensure_worker_raises_the_typed_auth_error() -> None:
    """``ActiveTools.call_tool`` reaches a lazy connect, not a KeyError.

    A server blocked at acquire time has no worker, so the old ``call_tool``
    raised ``KeyError`` and call-time acquisition could not work at all.
    """
    manager = MCPClientManager()
    entry = MCPServerEntry(name="workiq", transport="streamable_http", url="https://x")
    entry.error = "WorkIQ sign-in expired."
    manager._servers = {"workiq": entry}

    def _flag() -> None:
        entry.state = "needs_auth"

    _patch_failing_transport(manager, RuntimeError("Sign-in required."), before=_flag)

    bundle = ActiveTools(manager=manager)
    raised: MCPToolAuthRequired | None = None
    try:
        await bundle.call_tool("workiq", "search", {})
    except MCPToolAuthRequired as exc:
        raised = exc
    assert raised is not None
    assert raised.server == "workiq"
    assert raised.message == "WorkIQ sign-in expired."
    await manager.aclose()


async def test_lazy_connect_surfaces_a_plain_failure_untouched() -> None:
    """A server that is merely broken must not masquerade as needing a sign-in."""
    manager = MCPClientManager()
    entry = MCPServerEntry(name="byo", transport="stdio", command="x")
    manager._servers = {"byo": entry}

    _patch_failing_transport(manager, RuntimeError("command not found"))

    bundle = ActiveTools(manager=manager)
    raised = ""
    try:
        await bundle.call_tool("byo", "anything", {})
    except MCPToolAuthRequired:  # pragma: no cover - the bug this guards against
        raised = "auth"
    except RuntimeError as exc:
        raised = str(exc)
    assert "command not found" in raised
    await manager.aclose()


async def test_wait_is_re_polled_rather_than_one_long_block() -> None:
    """The pause is capped per iteration so a missed global signal self-heals.

    ``signal_auth_resolved`` is process-wide: a concurrent turn's sign-in, or the
    keep-alive's silent renewal, can fire between the raise and the moment this
    turn registers its waiter. A single ``wait_for_auth(300)`` would sleep
    through that and park the turn for the whole window -- the exact stall this
    change exists to remove -- so the wait is re-checked every ten seconds and
    the call retried on each pass.
    """
    manager = MCPClientManager()
    state = {"signed_in": False}

    async def responder(server: str, raw_name: str, args: dict[str, Any]) -> Any:
        _ = args
        if not state["signed_in"]:
            raise MCPToolAuthRequired(server, "WorkIQ sign-in expired.")
        return {"content": [], "raw": raw_name}

    asked: list[float] = []

    async def fake_wait(timeout: float) -> None:
        # Stand in for the signal this turn would otherwise have missed.
        asked.append(timeout)
        state["signed_in"] = True

    manager.wait_for_auth = fake_wait  # type: ignore[assignment]
    bundle = _blocked_bundle(manager, tools=[_tool()], responder=responder)

    events = await _drive(bundle, _OneToolProvider("workiq__search"), auth_wait_timeout=300.0)

    # Capped at ten seconds, not the full 300s window.
    assert asked == [10.0]
    assert len([e for e in events if isinstance(e, ToolAuthRequired)]) == 1
    assert [e.is_error for e in events if isinstance(e, ToolResultTurn)] == [False]


async def test_prompt_is_emitted_once_across_several_retries() -> None:
    """Re-polling must not re-prompt on every pass."""
    manager = MCPClientManager()

    async def responder(server: str, raw_name: str, args: dict[str, Any]) -> Any:
        _ = raw_name, args
        raise MCPToolAuthRequired(server, "WorkIQ sign-in expired.")

    waits = {"n": 0}

    async def fake_wait(timeout: float) -> None:
        _ = timeout
        waits["n"] += 1

    manager.wait_for_auth = fake_wait  # type: ignore[assignment]
    bundle = _blocked_bundle(manager, tools=[_tool()], responder=responder)

    events = await _drive(bundle, _OneToolProvider("workiq__search"), auth_wait_timeout=0.25)

    assert waits["n"] >= 1  # it really did re-poll
    assert len([e for e in events if isinstance(e, ToolAuthRequired)]) == 1
    assert [e.is_error for e in events if isinstance(e, ToolResultTurn)] == [True]
