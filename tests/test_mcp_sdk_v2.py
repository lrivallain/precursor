"""Precursor on MCP 2, exercised end to end (lrivallain/precursor#337).

The 2026.9.1 release candidate resolved ``mcp 2.2.0`` in a fresh wheel install
and could not even import. The unit suites never noticed: CI ran on the lockfile,
the built-in stdio servers are only imported inside their own subprocess, and
the tool-result doubles spelled MCP 1's camelCase fields. These tests use the
real SDK instead of doubles:

- every built-in stdio server module imports and builds an ``MCPServer``;
- a built-in stdio server is spawned as a subprocess, listed and called, then
  recycled and closed;
- the app's own streamable-HTTP ``/mcp`` endpoint is served by uvicorn and
  reached through Precursor's client, including the pooled worker's reconnect
  and the HTTP status MCP 2 stops reporting.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import socket
from collections.abc import AsyncIterator
from importlib.metadata import version
from typing import Any

import httpx
import pytest
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.types import INTERNAL_ERROR, INVALID_PARAMS, INVALID_REQUEST, CallToolResult

from precursor.backend.config import get_settings
from precursor.backend.db import init_db
from precursor.backend.services.mcp.client import (
    BUILTIN_CATALOG,
    MCPClientManager,
    _find_in_exception,
    _http_status_in,
    describe_transport_failure,
    is_transport_failure,
)
from precursor.backend.services.mcp.transport import MCPHTTPStatusError, streamable_http_session


def test_installed_sdk_is_mcp_2() -> None:
    """The code below is written against MCP 2 only; say so if that ever drifts."""
    assert version("mcp").split(".")[0] == "2"


_IN_TREE_SERVER_MODULES = [
    spec.args[1]
    for spec in BUILTIN_CATALOG
    if spec.transport == "stdio" and spec.args[:1] == ("-m",)
]


def test_the_in_tree_stdio_servers_are_all_covered() -> None:
    # Derived from the catalogue so a new built-in is covered without editing
    # this file; this just proves the derivation found them.
    assert "precursor.backend.services.mcp.precursor_server" in _IN_TREE_SERVER_MODULES
    assert len(_IN_TREE_SERVER_MODULES) >= 5


@pytest.mark.parametrize("module_name", _IN_TREE_SERVER_MODULES)
def test_builtin_stdio_server_module_builds_an_mcp_server(module_name: str) -> None:
    """The app never imports these; only their subprocess does, so check here."""
    module = importlib.import_module(module_name)
    servers = [value for value in vars(module).values() if isinstance(value, MCPServer)]
    assert servers, f"{module_name} exposes no MCPServer instance"


# --------------------------------------------------------------------------
# stdio
# --------------------------------------------------------------------------


async def test_stdio_builtin_lists_calls_recycles_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await init_db()
    manager = MCPClientManager()
    entry = manager.get("precursor")
    assert entry is not None and entry.transport == "stdio"

    async with manager.open_session("precursor") as (session, tools):
        names = {t.name for t in tools}
        assert {"precursor_info", "list_topics"} <= names
        listed = next(t for t in tools if t.name == "list_topics")
        assert listed.input_schema.get("type") == "object"

        result = await session.call_tool("precursor_info", {})
        assert isinstance(result, CallToolResult)
        assert result.is_error is False
        assert isinstance(result.structured_content, dict)
        assert result.structured_content["name"] == "precursor"
    assert entry.state == "ready"

    # The warm pool: call, drop the session as a dead transport would, call again.
    bundle = await manager.acquire(["precursor"])
    try:
        assert not bundle.unavailable
        first = await bundle.call_tool("precursor", "precursor_info", {})
        assert first.is_error is False
        worker = bundle.workers["precursor"]
        await manager.recycle_worker("precursor", worker)
        assert not worker.alive
        bundle.workers.pop("precursor")
        second = await bundle.call_tool("precursor", "precursor_info", {})
        assert second.is_error is False
        assert bundle.workers["precursor"] is not worker
    finally:
        await manager.aclose()
    assert all(not w.alive for w in bundle.workers.values())


async def test_stdio_tool_error_surfaces_as_an_error_result() -> None:
    """``is_error`` is what the tool loop reads; MCP 1 called it ``isError``."""
    await init_db()
    manager = MCPClientManager()
    async with manager.open_session("precursor") as (session, _tools):
        result = await session.call_tool("get_topic", {"topic_id": "not-an-int"})
    assert result.is_error is True


# --------------------------------------------------------------------------
# streamable HTTP — the app's own /mcp endpoint, served for real
# --------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.asynccontextmanager
async def _serve_app(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[str, Any]]:
    """Run ``create_app()`` under uvicorn on a free loopback port; yield it and its origin.

    The endpoint's Host allowlist is built from the configured port, so the port
    is patched in before the app is built, exactly as a real bind would set it.
    """
    from precursor.backend.main import create_app

    port = _free_port()
    monkeypatch.setattr(get_settings(), "port", port)
    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            if task.done():
                task.result()
            await asyncio.sleep(0.05)
        assert server.started, "uvicorn did not start"
        yield f"http://127.0.0.1:{port}", app
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=15)


async def _set_http_enabled(origin: str, enabled: bool) -> None:
    async with httpx.AsyncClient(base_url=origin) as client:
        response = await client.put("/api/settings", json={"mcp_http_enabled": enabled})
        response.raise_for_status()


async def test_streamable_http_lists_calls_and_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await init_db()
    async with _serve_app(monkeypatch) as (origin, _app):
        await _set_http_enabled(origin, True)
        manager = MCPClientManager()
        manager.register_user_entry(
            name="self-http", transport="streamable_http", url=f"{origin}/mcp"
        )
        try:
            async with manager.open_session("self-http") as (session, tools):
                assert {"precursor_info", "list_topics"} <= {t.name for t in tools}
                result = await session.call_tool("precursor_info", {})
                assert result.is_error is False
                assert result.structured_content["name"] == "precursor"

            bundle = await manager.acquire(["self-http"])
            assert not bundle.unavailable
            assert (await bundle.call_tool("self-http", "precursor_info", {})).is_error is False
            worker = bundle.workers["self-http"]
            await manager.recycle_worker("self-http", worker)
            bundle.workers.pop("self-http")
            again = await bundle.call_tool("self-http", "precursor_info", {})
            assert again.is_error is False
            assert bundle.workers["self-http"] is not worker
        finally:
            await manager.aclose()
            await _set_http_enabled(origin, False)


async def test_streamable_http_error_keeps_its_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP 2 turns a non-2xx POST into a status-less ``MCPError``; we restore it.

    With the endpoint switched off the gate answers 404, which is what a remote
    that has forgotten our session looks like — the case the pool retries.
    """
    await init_db()
    async with _serve_app(monkeypatch) as (origin, _app):
        await _set_http_enabled(origin, False)
        manager = MCPClientManager()
        manager.register_user_entry(
            name="self-http", transport="streamable_http", url=f"{origin}/mcp"
        )
        with pytest.raises(BaseException) as caught:
            async with manager.open_session("self-http"):
                pass
        await manager.aclose()

    assert _http_status_in(caught.value) == 404
    assert is_transport_failure(caught.value)
    assert "HTTP 404" in describe_transport_failure(caught.value)
    entry = manager.get("self-http")
    assert entry is not None and entry.state == "error"


async def test_a_server_that_forgot_the_session_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A remote restart, as MCP 2's own server reports it.

    The SDK's server answers an unknown ``Mcp-Session-Id`` with a 404 carrying a
    JSON-RPC body (``Session not found``), which MCP 2's client raises verbatim
    rather than as its ``Session terminated`` stand-in. The pool must still read
    it as a dead session and retry on a fresh one.
    """
    await init_db()
    async with _serve_app(monkeypatch) as (origin, app):
        await _set_http_enabled(origin, True)
        manager = MCPClientManager()
        manager.register_user_entry(
            name="self-http", transport="streamable_http", url=f"{origin}/mcp"
        )
        try:
            bundle = await manager.acquire(["self-http"])
            assert (await bundle.call_tool("self-http", "precursor_info", {})).is_error is False
            worker = bundle.workers["self-http"]

            # What a restarted endpoint looks like to the session we hold.
            app.state.precursor_http_mcp.session_manager._server_instances.clear()

            after = await bundle.call_tool("self-http", "precursor_info", {})
            assert after.is_error is False
            assert bundle.workers["self-http"] is not worker
        finally:
            await manager.aclose()
            await _set_http_enabled(origin, False)


@contextlib.asynccontextmanager
async def _serve_jsonrpc_error(status: int, code: int, message: str) -> AsyncIterator[str]:
    """Serve an endpoint that answers every POST with ``status`` and a JSON-RPC error body."""

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return
        body = b""
        while True:
            event = await receive()
            body += event.get("body", b"")
            if not event.get("more_body"):
                break
        request_id = json.loads(body or b"{}").get("id")
        payload = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": json.dumps(payload).encode()})

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            if task.done():
                task.result()
            await asyncio.sleep(0.05)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=15)


@pytest.mark.parametrize(
    ("status", "code", "message", "retried"),
    [
        (404, INVALID_REQUEST, "Session not found", True),
        (503, INTERNAL_ERROR, "Too many open sessions", True),
        (400, INVALID_PARAMS, "Invalid params", False),
    ],
)
async def test_a_json_rpc_error_body_keeps_its_http_status(
    status: int, code: int, message: str, retried: bool
) -> None:
    """The server's own error body is surfaced verbatim, and still tagged."""
    async with _serve_jsonrpc_error(status, code, message) as url:
        with pytest.raises(BaseException) as caught:
            async with streamable_http_session(url) as session:
                await session.initialize()

    tagged = _find_in_exception(caught.value, MCPHTTPStatusError)
    assert isinstance(tagged, MCPHTTPStatusError)
    assert (tagged.status, tagged.code) == (status, code)
    assert tagged.message == f"{message} (HTTP {status})"
    assert is_transport_failure(caught.value) is retried


def test_http_status_error_is_still_an_mcp_error() -> None:
    """Callers that catch the SDK's ``MCPError`` keep catching the tagged one."""
    from mcp import MCPError

    tagged = MCPHTTPStatusError(502, MCPError(INTERNAL_ERROR, "Server returned an error response"))
    assert isinstance(tagged, MCPError)
    assert tagged.code == INTERNAL_ERROR
    assert str(tagged) == "Server returned an error response (HTTP 502)"
