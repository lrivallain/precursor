"""Tests for the WorkIQ auth pause/resume gate and its supporting helpers.

Covers:
- ``_find_in_exception`` unwrapping ``WorkIQAuthRequiredError`` out of the
  anyio ``BaseExceptionGroup`` the MCP SDK wraps it in (the bug that kept the
  ``needs_auth`` banner from ever firing in the real path).
- The pause/resume primitives a held chat turn relies on:
  ``auth_blocked_servers`` / ``wait_for_auth`` / ``signal_auth_resolved``.
- The logging filter that suppresses the SDK's misleading ERROR traceback for
  an *expected* WorkIQ sign-in prompt while preserving genuine failures.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time

from precursor.backend.services.mcp.client import (
    MCPClientManager,
    MCPServerEntry,
    _describe_exception,
    _find_in_exception,
)
from precursor.backend.services.mcp.workiq_preview import (
    WorkIQAuthRequiredError,
    _SuppressExpectedAuthError,
)


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


def test_describe_exception_unwraps_task_group() -> None:
    # The reported symptom: a real error hidden behind anyio's group wrapper,
    # whose own ``str()`` is the useless "unhandled errors in a TaskGroup".
    group = BaseExceptionGroup(
        "unhandled errors in a TaskGroup (1 sub-exception)",
        [RuntimeError("Timed out waiting for the WorkIQ sign-in to complete.")],
    )
    assert _describe_exception(group) == "Timed out waiting for the WorkIQ sign-in to complete."


def test_describe_exception_joins_multiple_leaves() -> None:
    group = BaseExceptionGroup("grp", [RuntimeError("first failure"), ValueError("second failure")])
    assert _describe_exception(group) == "first failure; second failure"


def test_describe_exception_follows_cause_chain() -> None:
    inner = BaseExceptionGroup("inner", [RuntimeError("sign-in loopback closed")])
    try:
        try:
            raise inner
        except BaseException as ie:
            raise RuntimeError("transport closed") from ie
    except BaseException as chained:
        outer = BaseExceptionGroup("outer", [chained])
    assert _describe_exception(outer) == "transport closed; sign-in loopback closed"


def test_describe_exception_bare_and_empty_message() -> None:
    assert _describe_exception(RuntimeError("boom")) == "boom"
    # A message-less exception degrades to its type name, never a blank string.
    assert _describe_exception(ValueError()) == "ValueError"


def test_describe_exception_handles_cycles() -> None:
    err = RuntimeError("looping")
    err.__context__ = err  # must not loop forever
    assert _describe_exception(err) == "looping"


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


def _oauth_flow_error_record(exc: BaseException) -> logging.LogRecord:
    """Build the log record the SDK's ``logger.exception("OAuth flow error")`` emits."""
    try:
        raise exc
    except BaseException:
        exc_info = sys.exc_info()
    return logging.LogRecord(
        name="mcp.client.auth.oauth2",
        level=logging.ERROR,
        pathname=__file__,
        lineno=0,
        msg="OAuth flow error",
        args=(),
        exc_info=exc_info,
    )


def test_expected_auth_error_traceback_is_suppressed() -> None:
    # The SDK wraps callback errors in an anyio task group; the filter must still
    # recognise our expected sign-in prompt and drop the noisy stack trace.
    wrapped = BaseExceptionGroup(
        "unhandled errors in a TaskGroup (1 sub-exception)",
        [WorkIQAuthRequiredError("sign in")],
    )
    record = _oauth_flow_error_record(wrapped)
    assert _SuppressExpectedAuthError().filter(record) is False


def test_genuine_oauth_error_is_still_logged() -> None:
    record = _oauth_flow_error_record(RuntimeError("real transport failure"))
    assert _SuppressExpectedAuthError().filter(record) is True
