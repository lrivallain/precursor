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


def test_auth_blocked_servers_collapses_shared_credentials() -> None:
    """Servers behind one credential produce one prompt, not one each.

    The Agent 365 servers authenticate with a single token, so blocking on two
    of them used to ask the user to sign in twice for the same sign-in.
    """
    from precursor.backend.services.mcp import agent365

    teams, user = (spec.name for spec in agent365.AGENT365_SERVERS[:2])
    manager = MCPClientManager()
    manager._servers[teams] = _entry(teams, "needs_auth")
    manager._servers[user] = _entry(user, "needs_auth")
    manager._servers["workiq"] = _entry("workiq", "needs_auth")

    assert manager.auth_blocked_servers([teams, user]) == [teams]
    # A separate credential is still reported separately.
    assert manager.auth_blocked_servers(["workiq", teams, user]) == ["workiq", teams]


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


def _agent365_names() -> tuple[str, str]:
    """Two Agent 365 servers that share one credential."""
    from precursor.backend.services.mcp import agent365

    teams, user = (spec.name for spec in agent365.AGENT365_SERVERS[:2])
    return teams, user


async def test_open_transport_short_circuits_flagged_oauth_server() -> None:
    """A flagged OAuth connect fast-fails to ``needs_auth`` without connecting.

    ``mark_auth_required`` records the verdict; the next ``_open_transport`` for
    that credential raises ``WorkIQAuthRequiredError`` immediately instead of
    driving the doomed OAuth handshake (the seconds-long stall we're removing).
    """
    teams, _user = _agent365_names()
    manager = MCPClientManager()
    entry = _entry(teams, "disconnected")
    entry.auth_provider = object()  # stand-in for the non-interactive provider
    manager._servers[teams] = entry

    manager.mark_auth_required(teams, message="Sign-in expired.")
    assert entry.state == "needs_auth"

    raised = False
    try:
        async with manager._open_transport(teams):
            pass
    except WorkIQAuthRequiredError:
        raised = True
    assert raised
    assert entry.state == "needs_auth"
    assert entry.error == "Sign-in expired."


async def test_short_circuit_covers_credential_sibling() -> None:
    """Flagging one Agent 365 server short-circuits the one sharing its token."""
    teams, user = _agent365_names()
    manager = MCPClientManager()
    sibling = _entry(user, "disconnected")
    sibling.auth_provider = object()
    manager._servers[user] = sibling

    manager.mark_auth_required(teams)  # flag the *other* server on the credential

    raised = False
    try:
        async with manager._open_transport(user):
            pass
    except WorkIQAuthRequiredError:
        raised = True
    assert raised
    assert sibling.state == "needs_auth"


async def test_clear_auth_required_lets_connect_proceed() -> None:
    """Clearing the verdict removes the short-circuit for the credential."""
    teams, _user = _agent365_names()
    manager = MCPClientManager()
    entry = _entry(teams, "disconnected")
    entry.auth_provider = object()
    manager._servers[teams] = entry

    manager.mark_auth_required(teams)
    assert manager._auth_short_circuited(teams)
    manager.clear_auth_required(teams)
    assert not manager._auth_short_circuited(teams)


def test_signal_auth_resolved_clears_short_circuit() -> None:
    teams, _user = _agent365_names()
    manager = MCPClientManager()
    manager.mark_auth_required(teams)
    assert manager._auth_short_circuit

    manager.signal_auth_resolved()
    assert not manager._auth_short_circuit


def test_short_circuit_ignores_non_oauth_server() -> None:
    """A plain server with a stray flag is never fast-failed as OAuth."""
    manager = MCPClientManager()
    entry = _entry("byo", "ready")
    entry.auth_provider = None  # non-OAuth server has no provider
    manager._servers["byo"] = entry

    manager.mark_auth_required("byo")
    # No auth_provider → the short-circuit branch is skipped entirely; the flag
    # is inert for a plain server.
    assert manager._auth_short_circuited("byo")


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
