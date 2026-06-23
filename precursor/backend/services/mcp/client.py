"""Inbound MCP — Precursor as a client to external tool servers.

Today we ship one built-in server (GitHub, remote streamable-http at
``https://api.githubcopilot.com/mcp``). The same machinery is designed to host
additional built-ins (work-iq) and user-defined BYO servers later. Sessions are
kept *warm* in a small per-server pool (see ``MCPClientManager.acquire`` and
``_ServerWorker``): each server's session is opened once and reused across chat
turns until it goes idle, so the tool loop no longer pays connect + initialize +
list_tools on every message. A one-shot ``open_session`` remains for the catalog
probe.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from precursor.backend.config import get_settings

logger = logging.getLogger(__name__)

ConnectionState = Literal[
    "disconnected",
    "connecting",
    "connected",
    "ready",
    "error",
    "disabled",
]


@dataclass(slots=True)
class MCPToolDef:
    """An MCP tool exposed by a server, in a transport-agnostic shape."""

    server: str
    name: str
    description: str
    input_schema: dict[str, Any]

    @property
    def qualified_name(self) -> str:
        # OpenAI tool names must match ``^[a-zA-Z0-9_-]+$`` and be unique
        # per request, so we namespace ``server__tool``.
        return f"{self.server}__{self.name}"


HeadersProvider = Callable[[str], dict[str, str] | None]


@dataclass(slots=True)
class MCPServerEntry:
    name: str
    transport: Literal["streamable_http", "stdio"]
    url: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    # Extra environment for stdio subprocesses. None => MCP SDK's minimal
    # default env. Built-ins that need the app's DB/config forward os.environ.
    env: dict[str, str] | None = None
    headers_provider: HeadersProvider | None = None
    builtin: bool = True
    state: ConnectionState = "disconnected"
    error: str | None = None
    tools: list[MCPToolDef] = field(default_factory=list)


class MCPClientManager:
    """Registry of configured MCP servers + per-turn session opener."""

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerEntry] = {}
        # Warm-session pool: one long-lived worker task per server name, kept
        # alive across turns so we don't pay connect+initialize+list_tools on
        # every message. Guarded by a lock so concurrent turns don't start
        # duplicate workers for the same server.
        self._workers: dict[str, _ServerWorker] = {}
        self._pool_lock = asyncio.Lock()
        # Strong refs to fire-and-forget worker-retirement tasks so the GC
        # doesn't cancel them mid-teardown.
        self._retiring: set[asyncio.Task[None]] = set()
        self._register_builtins()

    def _register_builtins(self) -> None:
        # GitHub MCP — remote streamable-http. Auth header is resolved lazily
        # so adding a token after startup works without a restart.
        self._servers["github"] = MCPServerEntry(
            name="github",
            transport="streamable_http",
            url="https://api.githubcopilot.com/mcp/",
            headers_provider=_github_headers,
            builtin=True,
        )
        # WorkIQ MCP — local stdio launcher. The npm package handles its own
        # interactive auth on first run.
        self._servers["workiq"] = MCPServerEntry(
            name="workiq",
            transport="stdio",
            command="npx",
            args=["-y", "@microsoft/workiq@latest", "mcp"],
            builtin=True,
        )
        # Fetch MCP — in-tree stdio subprocess that exposes curl-like HTTP
        # tools (http_get / http_request). Uses the same Python interpreter
        # that runs the backend so the package is always importable.
        self._servers["fetch"] = MCPServerEntry(
            name="fetch",
            transport="stdio",
            command=sys.executable,
            args=["-m", "precursor.backend.services.mcp.fetch_server"],
            builtin=True,
        )
        # Workspace filesystem MCP — in-tree stdio subprocess exposing
        # sandboxed read/write tools over Workspace working trees. It needs the
        # app's DB + config to resolve a workspace to its on-disk path, so we
        # forward the current environment (PRECURSOR_*, GITHUB_TOKEN, …) and
        # rely on the inherited CWD for .env / relative paths.
        self._servers["workspace-fs"] = MCPServerEntry(
            name="workspace-fs",
            transport="stdio",
            command=sys.executable,
            args=["-m", "precursor.backend.services.mcp.workspace_fs_server"],
            env=dict(os.environ),
            builtin=True,
        )
        # Command runner MCP — in-tree stdio subprocess that runs bash/python/
        # node either inside a Docker "jail" (default) or directly on the host.
        # It forwards the env (DB/config). Enable-time Docker availability is
        # checked in the connect router (it needs a DB session to read the
        # effective jail setting).
        self._servers["cmd-runner"] = MCPServerEntry(
            name="cmd-runner",
            transport="stdio",
            command=sys.executable,
            args=["-m", "precursor.backend.services.mcp.cmd_runner_server"],
            env=dict(os.environ),
            builtin=True,
        )
        # Precursor MCP — in-tree stdio subprocess exposing Precursor's *own*
        # capabilities (topics, messages, search, skills, memory, post_message,
        # schedules) outbound. The same entrypoint serves the in-app agent and
        # external MCP hosts. Every tool is gated by a per-section toggle
        # (mcp_expose) read from the DB at call time, so nothing is served until
        # the user opts in. Forwards the env so the subprocess reaches the DB.
        self._servers["precursor"] = MCPServerEntry(
            name="precursor",
            transport="stdio",
            command=sys.executable,
            args=["-m", "precursor.backend.services.mcp.precursor_server"],
            env=dict(os.environ),
            builtin=True,
        )

    def get(self, name: str) -> MCPServerEntry | None:
        return self._servers.get(name)

    def list_entries(self) -> list[MCPServerEntry]:
        return list(self._servers.values())

    def register_user_entry(
        self,
        *,
        name: str,
        transport: Literal["streamable_http", "stdio"],
        url: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> MCPServerEntry:
        """Register a user-defined entry, replacing any existing one with the same name.

        Rejects names that collide with built-in entries to keep the catalog
        addressable by name from the chat router and toggles.
        """
        existing = self._servers.get(name)
        if existing is not None and existing.builtin:
            raise ValueError(f"'{name}' is reserved by a built-in MCP server")
        static_headers = dict(headers) if headers else None
        provider: HeadersProvider | None = (
            (lambda _token, h=static_headers: dict(h)) if static_headers else None
        )
        entry = MCPServerEntry(
            name=name,
            transport=transport,
            url=url,
            command=command,
            args=list(args or []),
            headers_provider=provider,
            builtin=False,
        )
        self._servers[name] = entry
        return entry

    def unregister_user_entry(self, name: str) -> bool:
        entry = self._servers.get(name)
        if entry is None or entry.builtin:
            return False
        del self._servers[name]
        return True

    @asynccontextmanager
    async def open_session(
        self, name: str, *, github_token: str = ""
    ) -> AsyncIterator[tuple[ClientSession, list[MCPToolDef]]]:
        """Open a one-shot live MCP session against a configured server.

        Yields ``(session, tools)``; caller must use as ``async with``. Used for
        the catalog probe and any path that wants a throwaway session. The chat
        tool loop uses :meth:`acquire` instead, which keeps sessions warm.
        """
        async with self._open_transport(name, github_token=github_token) as (session, tools):
            yield session, tools

    @asynccontextmanager
    async def _open_transport(
        self, name: str, *, github_token: str = ""
    ) -> AsyncIterator[tuple[ClientSession, list[MCPToolDef]]]:
        """Open the transport + initialized session for ``name``.

        Shared by the one-shot :meth:`open_session` and the warm-pool worker.
        Updates ``entry.state``/``entry.tools`` so the Settings UI reflects
        connectivity either way.
        """
        entry = self._servers.get(name)
        if entry is None:
            raise KeyError(f"Unknown MCP server: {name}")

        entry.state = "connecting"
        entry.error = None
        try:
            if entry.transport == "streamable_http":
                if not entry.url:
                    raise RuntimeError(f"MCP server '{name}' has no URL configured")
                headers = entry.headers_provider(github_token) if entry.headers_provider else None
                if entry.headers_provider and headers is None:
                    raise RuntimeError(
                        f"MCP server '{name}' has no credentials; configure them in Settings"
                    )
                async with (
                    streamablehttp_client(entry.url, headers=headers) as (
                        read,
                        write,
                        _get_session_id,
                    ),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    tools = await self._fetch_tools(name, session)
                    entry.tools = tools
                    entry.state = "connected"
                    yield session, tools
            elif entry.transport == "stdio":
                if not entry.command:
                    raise RuntimeError(f"MCP server '{name}' has no command configured")
                params = StdioServerParameters(
                    command=entry.command, args=list(entry.args), env=entry.env
                )
                async with (
                    stdio_client(params) as (read, write),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    tools = await self._fetch_tools(name, session)
                    entry.tools = tools
                    entry.state = "connected"
                    yield session, tools
            else:
                raise RuntimeError(
                    f"MCP server '{name}' has unsupported transport {entry.transport!r}"
                )
        except Exception as exc:
            entry.state = "error"
            entry.error = str(exc)
            logger.warning("MCP session for %s failed: %s", name, exc)
            raise
        finally:
            # The transport session is closed when the context exits, but for
            # the UI a successful initialize+list_tools means the server is
            # usable on the next turn. Surface that as "ready" instead of
            # flapping back to "disconnected".
            if entry.state == "connected":
                entry.state = "ready"

    async def acquire(self, server_names: list[str], *, github_token: str = "") -> ActiveTools:
        """Return aggregated tools for ``server_names`` over warm sessions.

        Starts (or reuses) one long-lived worker per server, waits for them to
        become ready concurrently, and returns an :class:`ActiveTools` bundle
        whose :meth:`ActiveTools.call_tool` routes to the right warm session.
        Servers that fail to start are reported via ``unavailable`` rather than
        raising, mirroring the previous best-effort per-server behaviour.
        """
        pool_disabled = get_settings().mcp_idle_ttl_seconds <= 0
        bundle = ActiveTools(manager=self, ephemeral=pool_disabled)

        async with self._pool_lock:
            targets: dict[str, _ServerWorker] = {}
            for name in server_names:
                entry = self._servers.get(name)
                worker = self._workers.get(name)
                token_stale = (
                    entry is not None
                    and entry.headers_provider is not None
                    and worker is not None
                    and worker.github_token != github_token
                )
                if worker is None or not worker.alive or token_stale or pool_disabled:
                    if worker is not None:
                        # Retire the stale/dead worker without blocking startup.
                        retire = asyncio.create_task(worker.aclose())
                        self._retiring.add(retire)
                        retire.add_done_callback(self._retiring.discard)
                    worker = _ServerWorker(self, name, github_token)
                    if not pool_disabled:
                        self._workers[name] = worker
                targets[name] = worker

        results = await asyncio.gather(
            *(w.wait_ready() for w in targets.values()), return_exceptions=True
        )
        for (name, worker), result in zip(targets.items(), results, strict=True):
            if isinstance(result, BaseException):
                bundle.unavailable.append((name, str(result)))
                async with self._pool_lock:
                    if self._workers.get(name) is worker:
                        del self._workers[name]
                continue
            for tool in result:
                bundle.tools.append(tool)
                bundle.tool_to_server[tool.qualified_name] = (name, tool.name)
                bundle.workers[name] = worker
        return bundle

    async def aclose(self) -> None:
        """Tear down every warm worker (called on app shutdown)."""
        async with self._pool_lock:
            workers = list(self._workers.values())
            self._workers.clear()
        if workers:
            await asyncio.gather(*(w.aclose() for w in workers), return_exceptions=True)

    @asynccontextmanager
    async def acquired(
        self, server_names: list[str], *, github_token: str = ""
    ) -> AsyncIterator[ActiveTools]:
        """Context-manager flavour of :meth:`acquire`.

        Yields the :class:`ActiveTools` bundle for the turn. When pooling is
        enabled, exiting leaves the sessions warm for the next turn (only
        :meth:`aclose` or idle expiry tears them down). When pooling is disabled
        the bundle is ephemeral, so exiting closes its one-shot sessions.
        """
        bundle = await self.acquire(server_names, github_token=github_token)
        try:
            yield bundle
        finally:
            if bundle.ephemeral:
                await bundle.aclose()

    async def _fetch_tools(self, server_name: str, session: ClientSession) -> list[MCPToolDef]:
        result = await session.list_tools()
        return [
            MCPToolDef(
                server=server_name,
                name=t.name,
                description=(t.description or "").strip(),
                input_schema=t.inputSchema or {"type": "object", "properties": {}},
            )
            for t in result.tools
        ]

    async def probe(self, name: str, *, github_token: str = "") -> MCPServerEntry:
        """Open + close a session purely to refresh the catalog/state for the UI."""
        entry = self._servers.get(name)
        if entry is None:
            raise KeyError(name)
        try:
            async with self.open_session(name, github_token=github_token):
                pass
        except Exception:
            pass
        return entry

    def status_dict(self, entry: MCPServerEntry, *, enabled: bool) -> dict[str, Any]:
        command_str: str | None = None
        if entry.transport == "stdio" and entry.command:
            command_str = " ".join([entry.command, *entry.args])
        return {
            "name": entry.name,
            "transport": entry.transport,
            "url": entry.url,
            "command": command_str,
            # Raw stdio command + args (for the editor — built-ins keep them too,
            # but the UI ignores them since builtin=true means read-only).
            "command_bin": entry.command,
            "args": list(entry.args),
            "state": "disabled" if not enabled else entry.state,
            "error": entry.error,
            "tools": [{"name": t.name, "description": t.description} for t in entry.tools],
            "builtin": entry.builtin,
            "enabled": enabled,
        }


def _github_headers(token: str) -> dict[str, str] | None:
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "X-MCP-Toolsets": get_settings().github_mcp_toolsets,
    }


@dataclass(slots=True)
class _ToolCall:
    raw_name: str
    args: dict[str, Any]
    future: asyncio.Future[Any]


class _ServerWorker:
    """Owns a long-lived MCP session inside a dedicated task.

    The session is opened *and* closed in the same task (an anyio requirement —
    the SDK's transports bind cancel scopes to their owning task), so callers on
    other tasks reach it only by enqueueing tool calls. The session is held warm
    until idle for ``mcp_idle_ttl_seconds`` or until :meth:`aclose`.
    """

    def __init__(self, manager: MCPClientManager, name: str, github_token: str) -> None:
        self._manager = manager
        self.name = name
        self.github_token = github_token
        self._queue: asyncio.Queue[_ToolCall | None] = asyncio.Queue()
        self._ready: asyncio.Future[list[MCPToolDef]] = asyncio.get_running_loop().create_future()
        self._task = asyncio.create_task(self._run())

    @property
    def alive(self) -> bool:
        return not self._task.done()

    async def wait_ready(self) -> list[MCPToolDef]:
        """Block until the session has initialized; returns its tools or raises."""
        return await self._ready

    async def call(self, raw_name: str, args: dict[str, Any]) -> Any:
        if self._task.done():
            raise RuntimeError(f"MCP server '{self.name}' session is not running")
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        await self._queue.put(_ToolCall(raw_name=raw_name, args=args, future=future))
        return await future

    async def aclose(self) -> None:
        if self._task.done():
            return
        await self._queue.put(None)  # graceful-shutdown sentinel
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=10)
        except (TimeoutError, asyncio.CancelledError):
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task

    async def _run(self) -> None:
        idle_ttl = get_settings().mcp_idle_ttl_seconds
        timeout = idle_ttl if idle_ttl > 0 else None
        try:
            async with self._manager._open_transport(self.name, github_token=self.github_token) as (
                session,
                tools,
            ):
                if not self._ready.done():
                    self._ready.set_result(tools)
                while True:
                    try:
                        item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                    except TimeoutError:
                        break  # idle → release the warm session
                    if item is None:
                        break  # shutdown sentinel
                    if item.future.done():
                        continue  # caller already gave up
                    try:
                        result = await session.call_tool(item.raw_name, item.args)
                    except Exception as exc:
                        if not item.future.done():
                            item.future.set_exception(exc)
                    else:
                        if not item.future.done():
                            item.future.set_result(result)
        except BaseException as exc:
            if not self._ready.done():
                self._ready.set_exception(exc)
        finally:
            self._fail_pending(RuntimeError(f"MCP server '{self.name}' session closed"))

    def _fail_pending(self, exc: Exception) -> None:
        """Reject any still-queued calls so callers don't hang after teardown."""
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is not None and not item.future.done():
                item.future.set_exception(exc)


@dataclass(slots=True)
class ActiveTools:
    """Aggregated, ready-to-use tools backed by warm sessions for one turn."""

    manager: MCPClientManager
    tools: list[MCPToolDef] = field(default_factory=list)
    # qualified tool name -> (server, raw tool name)
    tool_to_server: dict[str, tuple[str, str]] = field(default_factory=dict)
    # server name -> worker serving its calls
    workers: dict[str, _ServerWorker] = field(default_factory=dict)
    # (server, error) for servers that failed to start this turn
    unavailable: list[tuple[str, str]] = field(default_factory=list)
    # True when pooling is disabled: the workers are one-shot and the caller
    # (or the ``acquired`` context manager) must close them at turn end.
    ephemeral: bool = False

    async def call_tool(self, server: str, raw_name: str, args: dict[str, Any]) -> Any:
        worker = self.workers.get(server)
        if worker is None:
            raise KeyError(f"No active MCP session for server '{server}'")
        return await worker.call(raw_name, args)

    async def aclose(self) -> None:
        """Close the workers backing this bundle (only used when ephemeral)."""
        workers = list(self.workers.values())
        if workers:
            await asyncio.gather(*(w.aclose() for w in workers), return_exceptions=True)


@lru_cache
def get_mcp_client_manager() -> MCPClientManager:
    return MCPClientManager()
