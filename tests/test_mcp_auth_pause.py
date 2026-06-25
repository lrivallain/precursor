"""Tests for the WorkIQ auth pause/resume gate and its supporting helpers.

Covers:
- ``_find_in_exception`` unwrapping ``WorkIQAuthRequiredError`` out of the
  anyio ``BaseExceptionGroup`` the MCP SDK wraps it in (the bug that kept the
  ``needs_auth`` banner from ever firing in the real path).
- The pause/resume primitives a held chat turn relies on:
  ``auth_blocked_servers`` / ``wait_for_auth`` / ``signal_auth_resolved``.
"""

from __future__ import annotations

import asyncio
import time

from precursor.backend.services.mcp.client import (
    MCPClientManager,
    MCPServerEntry,
    _find_in_exception,
)
from precursor.backend.services.mcp.workiq_preview import WorkIQAuthRequiredError


def test_find_in_exception_bare() -> None:
    err = WorkIQAuthRequiredError("sign in")
    assert _find_in_exception(err, WorkIQAuthRequiredError) is err


def test_find_in_exception_unwraps_task_group() -> None:
    # The shape anyio's task group raises: a single sub-exception wrapped in a
    # BaseExceptionGroup titled "unhandled errors in a TaskGroup".
    err = WorkIQAuthRequiredError("sign in")
    group = BaseExceptionGroup("unhandled errors in a TaskGroup (1 sub-exception)", [err])
    assert _find_in_exception(group, WorkIQAuthRequiredError) is err


def test_find_in_exception_unwraps_nested_group_and_chain() -> None:
    err = WorkIQAuthRequiredError("sign in")
    inner = BaseExceptionGroup("inner", [err])
    try:
        try:
            raise inner
        except BaseException as ie:
            raise RuntimeError("transport closed") from ie
    except BaseException as chained:
        outer = BaseExceptionGroup("outer", [chained])
    assert _find_in_exception(outer, WorkIQAuthRequiredError) is err


def test_find_in_exception_absent_returns_none() -> None:
    group = BaseExceptionGroup("g", [RuntimeError("unrelated")])
    assert _find_in_exception(group, WorkIQAuthRequiredError) is None


def test_find_in_exception_handles_cycles() -> None:
    err = RuntimeError("a")
    err.__context__ = err  # self-referential chain must not loop forever
    assert _find_in_exception(err, WorkIQAuthRequiredError) is None


def _entry(name: str, state: str) -> MCPServerEntry:
    entry = MCPServerEntry(name=name, transport="streamable_http", url="https://example")
    entry.state = state  # type: ignore[assignment]
    return entry


def test_auth_blocked_servers_filters_needs_auth() -> None:
    manager = MCPClientManager()
    manager._servers["workiq"] = _entry("workiq", "needs_auth")
    manager._servers["github"] = _entry("github", "ready")

    assert manager.auth_blocked_servers(["workiq", "github"]) == ["workiq"]
    assert manager.auth_blocked_servers(["github"]) == []
    assert manager.auth_blocked_servers(["missing"]) == []


async def test_wait_for_auth_wakes_on_signal() -> None:
    manager = MCPClientManager()

    async def resolve_soon() -> None:
        await asyncio.sleep(0.05)
        manager.signal_auth_resolved()

    start = time.perf_counter()
    await asyncio.gather(manager.wait_for_auth(timeout=5.0), resolve_soon())
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0
    assert not manager._auth_waiters  # waiter cleaned up


async def test_wait_for_auth_times_out_and_cleans_up() -> None:
    manager = MCPClientManager()
    start = time.perf_counter()
    await manager.wait_for_auth(timeout=0.1)
    elapsed = time.perf_counter() - start

    assert elapsed >= 0.1
    assert not manager._auth_waiters


def test_signal_with_no_waiters_is_noop() -> None:
    manager = MCPClientManager()
    manager.signal_auth_resolved()  # must not raise
