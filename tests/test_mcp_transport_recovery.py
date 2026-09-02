"""Tests for recovering from an MCP session that dies at the transport level.

The reported symptom: a flapping remote endpoint (Agent 365) answered POSTs with
HTTP 404/502, which the MCP SDK surfaces as the JSON-RPC error ``Session
terminated``. The warm ``_ServerWorker`` kept looping on the now-poisoned
session, so *every* remaining tool call in the turn failed identically, and the
bare "Session terminated" text led the model to re-plan the call with smaller
arguments and then blame an expired sign-in.

Covers:
- ``is_transport_failure`` / ``describe_transport_failure`` classifying a dead
  session (remote 404, any 5xx, socket teardown) apart from a genuine tool error.
- ``ActiveTools.call_tool`` recycling the poisoned worker and retrying once.
- That retry being bounded (no loop) when the endpoint is really down.
- ``recycle_worker`` leaving a *replacement* worker alone.
- The tool loop surfacing an explanation the model can act on.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx

from precursor.backend.services.mcp.client import (
    MCPClientManager,
    MCPToolDef,
    describe_transport_failure,
    is_transport_failure,
)
from precursor.backend.services.turn_engine import (
    ToolCallOutcome,
    call_tool_with_auth_retry,
)


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/mcp")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"error {status}", request=request, response=response)


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------


def test_session_terminated_is_a_transport_failure() -> None:
    # What the SDK synthesises when the endpoint 404s our Mcp-Session-Id.
    assert is_transport_failure(RuntimeError("Session terminated"))


def test_remote_404_and_5xx_are_transport_failures() -> None:
    assert is_transport_failure(_http_error(404))
    assert is_transport_failure(_http_error(502))
    assert is_transport_failure(_http_error(503))


def test_transport_failure_unwraps_task_group() -> None:
    # anyio wraps transport errors in a group; the real cause must still count.
    group = BaseExceptionGroup("unhandled errors in a TaskGroup", [_http_error(502)])
    assert is_transport_failure(group)


def test_dead_worker_is_a_transport_failure() -> None:
    # Raised by _ServerWorker once its session task has gone.
    assert is_transport_failure(RuntimeError("MCP server 'x' session is not running"))
    assert is_transport_failure(RuntimeError("MCP server 'x' session closed"))


def test_genuine_tool_errors_are_not_transport_failures() -> None:
    # A 4xx that isn't 404 is the server rejecting *this call*, not the session.
    assert not is_transport_failure(_http_error(400))
    assert not is_transport_failure(RuntimeError("chatId is required"))
    assert not is_transport_failure(RuntimeError("Unknown tool: ListChats"))


def test_describe_transport_failure_is_specific() -> None:
    assert "HTTP 502" in describe_transport_failure(_http_error(502))
    # The bare SDK wording is translated into what actually happened.
    described = describe_transport_failure(RuntimeError("Session terminated"))
    assert "404" in described and "no longer recognises" in described


# --------------------------------------------------------------------------
# recycle + retry
# --------------------------------------------------------------------------


class _Session:
    """Fake MCP session; raises ``error`` on every call when given one."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, args: dict):
        self.calls.append((name, args))
        if self.error is not None:
            raise self.error
        return {"ok": name, "args": args}


def _patch_transport(manager: MCPClientManager, sessions: list[_Session]) -> dict[str, int]:
    """Hand out ``sessions`` in order, one per transport open."""
    opens = {"count": 0}

    # The lazy reconnect path goes through ``ensure_worker``, which resolves the
    # server from the catalogue, so the entry has to exist as it would in
    # production.
    manager.register_user_entry(
        name="alpha", transport="streamable_http", url="https://example.test/mcp"
    )

    @asynccontextmanager
    async def fake_open(name: str, *, github_token: str = ""):
        session = sessions[min(opens["count"], len(sessions) - 1)]
        opens["count"] += 1
        yield (
            session,
            [
                MCPToolDef(
                    server=name,
                    name="ping",
                    description="",
                    input_schema={"type": "object", "properties": {}},
                )
            ],
        )

    manager._open_transport = fake_open  # type: ignore[assignment]
    return opens


async def test_dead_session_is_recycled_and_the_call_retried() -> None:
    dead = _Session(RuntimeError("Session terminated"))
    healthy = _Session()
    manager = MCPClientManager()
    opens = _patch_transport(manager, [dead, healthy])
    try:
        active = await manager.acquire(["alpha"])
        result = await active.call_tool("alpha", "ping", {"x": 1})

        # The caller sees a success, not the transport blip.
        assert result == {"ok": "ping", "args": {"x": 1}}
        # Retried on a *fresh* transport rather than the poisoned one.
        assert opens["count"] == 2
        assert len(dead.calls) == 1
        assert healthy.calls == [("ping", {"x": 1})]
    finally:
        await manager.aclose()


async def test_retry_is_bounded_when_the_endpoint_is_really_down() -> None:
    down = _Session(RuntimeError("Session terminated"))
    manager = MCPClientManager()
    opens = _patch_transport(manager, [down])
    try:
        active = await manager.acquire(["alpha"])
        try:
            await active.call_tool("alpha", "ping", {})
        except Exception as exc:
            assert is_transport_failure(exc)
        else:  # pragma: no cover - the call must not succeed
            raise AssertionError("expected the transport failure to propagate")

        # Exactly one retry: the original open plus one recycle. A loop here
        # would hammer a downed endpoint for the whole turn.
        assert opens["count"] == 2
        assert len(down.calls) == 2
    finally:
        await manager.aclose()


async def test_subsequent_calls_use_the_replacement_session() -> None:
    dead = _Session(RuntimeError("Session terminated"))
    healthy = _Session()
    manager = MCPClientManager()
    opens = _patch_transport(manager, [dead, healthy])
    try:
        active = await manager.acquire(["alpha"])
        await active.call_tool("alpha", "ping", {"n": 1})
        await active.call_tool("alpha", "ping", {"n": 2})

        # The second call reuses the recycled worker instead of reconnecting.
        assert opens["count"] == 2
        assert healthy.calls == [("ping", {"n": 1}), ("ping", {"n": 2})]
    finally:
        await manager.aclose()


async def test_recycle_worker_leaves_a_replacement_alone() -> None:
    manager = MCPClientManager()
    _patch_transport(manager, [_Session(), _Session()])
    try:
        stale = await manager.ensure_worker("alpha")
        await manager.retire_worker("alpha")
        replacement = await manager.ensure_worker("alpha")
        assert replacement is not stale

        # A late recycle of the stale worker must not evict the healthy one a
        # concurrent turn is already using.
        await manager.recycle_worker("alpha", stale)
        assert manager._workers.get("alpha") is replacement
        assert replacement.alive
    finally:
        await manager.aclose()


# --------------------------------------------------------------------------
# what the model is told
# --------------------------------------------------------------------------


class _FailingActive:
    """Minimal ActiveTools stand-in whose calls always fail with ``error``."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.manager = None

    async def call_tool(self, server: str, raw_name: str, args: dict):
        raise self.error


async def _run_tool_loop_step(error: Exception) -> ToolCallOutcome:
    outcomes = [
        step
        async for step in call_tool_with_auth_retry(
            active=_FailingActive(error),
            server="workiq-teams",
            raw_name="ListChats",
            tool_name="workiq-teams__ListChats",
            args={"topic": "Jack"},
            auth_wait_timeout=0,
        )
    ]
    assert len(outcomes) == 1
    assert isinstance(outcomes[0], ToolCallOutcome)
    return outcomes[0]


async def test_transport_failure_message_steers_the_model() -> None:
    outcome = await _run_tool_loop_step(RuntimeError("Session terminated"))
    text = outcome.result_text

    assert outcome.is_error
    # Names the server and the real reason instead of the opaque SDK wording.
    assert "workiq-teams" in text
    assert "404" in text
    # And rules out the two wrong turns the bare error provoked: re-planning the
    # arguments, and sending the user to a pointless re-authentication.
    assert "not a problem with the arguments" in text
    assert "do not suggest re-authenticating" in text


async def test_ordinary_tool_errors_keep_the_plain_message() -> None:
    outcome = await _run_tool_loop_step(RuntimeError("chatId is required"))
    assert outcome.is_error
    assert outcome.result_text == "Tool call failed: chatId is required"
