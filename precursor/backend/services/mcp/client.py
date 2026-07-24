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
import shutil
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from precursor.backend.config import get_settings

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

# How long a chat turn waits for an interactive MCP sign-in before giving up
# rather than answering without the required tools. Matches the OAuth callback
# window. Shared by the topic/chat and workspace streaming routers.
AUTH_PAUSE_TIMEOUT_SECONDS = 300.0


def _find_in_exception(exc: BaseException, exc_type: type[BaseException]) -> BaseException | None:
    """Locate an ``exc_type`` instance within ``exc``.

    The MCP SDK's streamable-http transport runs inside an anyio task group, so
    an exception raised in a callback (e.g. our ``WorkIQAuthRequiredError`` from
    the OAuth redirect handler) surfaces wrapped in a ``BaseExceptionGroup``.
    Walk the group members plus the ``__cause__``/``__context__`` chain so the
    real cause is still recognised instead of degrading to a generic error.
    """
    seen: set[int] = set()

    def _walk(node: BaseException | None) -> BaseException | None:
        if node is None or id(node) in seen:
            return None
        seen.add(id(node))
        if isinstance(node, exc_type):
            return node
        if isinstance(node, BaseExceptionGroup):
            for sub in node.exceptions:
                hit = _walk(sub)
                if hit is not None:
                    return hit
        for chained in (node.__cause__, node.__context__):
            hit = _walk(chained)
            if hit is not None:
                return hit
        return None

    return _walk(exc)


def _describe_exception(exc: BaseException) -> str:
    """Return a concise, human-readable summary of ``exc``.

    The MCP SDK's streamable-http transport runs inside an anyio task group, so
    a failed connect/sign-in surfaces as a ``BaseExceptionGroup`` whose ``str()``
    is the opaque "unhandled errors in a TaskGroup (N sub-exceptions)". Flatten
    the group into its leaf exceptions (following the ``__cause__``/``__context__``
    chain) and join their messages so callers can surface the real reason instead
    of the useless wrapper.
    """
    seen: set[int] = set()
    leaves: list[str] = []

    def _leaf_text(node: BaseException) -> str:
        message = str(node).strip()
        return message or type(node).__name__

    def _walk(node: BaseException | None) -> None:
        if node is None or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, BaseExceptionGroup):
            for sub in node.exceptions:
                _walk(sub)
            return
        text = _leaf_text(node)
        if text not in leaves:
            leaves.append(text)
        for chained in (node.__cause__, node.__context__):
            _walk(chained)

    _walk(exc)
    return "; ".join(leaves) if leaves else _leaf_text(exc)


ConnectionState = Literal[
    "disconnected",
    "connecting",
    "connected",
    "ready",
    "error",
    "needs_auth",
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
    # Optional httpx auth driver for streamable_http transports (e.g. the
    # WorkIQ preview OAuth provider). When set, the transport authenticates via
    # this instead of (or in addition to) ``headers_provider``.
    auth_provider: httpx.Auth | None = None
    builtin: bool = True
    state: ConnectionState = "disconnected"
    error: str | None = None
    tools: list[MCPToolDef] = field(default_factory=list)


def _github_headers(token: str) -> dict[str, str] | None:
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "X-MCP-Toolsets": get_settings().github_mcp_toolsets,
    }


@dataclass(frozen=True)
class _BuiltinSpec:
    """Declarative description of a built-in MCP server.

    ``_register_builtins`` turns each spec into an ``MCPServerEntry``. Adding a
    built-in is a one-line catalog entry rather than another inline block.
    """

    name: str
    transport: Literal["streamable_http", "stdio"]
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    headers_provider: HeadersProvider | None = None
    # Forward the app's environment (PRECURSOR_*, GITHUB_TOKEN, …) to the stdio
    # subprocess so in-tree servers can reach the DB/config.
    forward_env: bool = False


# The npx launcher for the built-in WorkIQ server in its default (local stdio)
# mode. Shared so ``configure_workiq_preview`` can revert to it from preview.
_WORKIQ_STDIO_COMMAND = "npx"
_WORKIQ_STDIO_ARGS: tuple[str, ...] = ("-y", "@microsoft/workiq@latest", "mcp")

# The npx launcher for the built-in Playwright server (Microsoft's official
# ``@playwright/mcp``). The ``--browser`` channel and an optional
# ``--user-data-dir`` are appended lazily in ``_register_builtins`` from
# settings. Headed by default (no ``--headless``) so the user can complete the
# first interactive sign-in; the persistent profile then reuses it.
_PLAYWRIGHT_STDIO_COMMAND = "npx"
_PLAYWRIGHT_STDIO_ARGS: tuple[str, ...] = (
    "-y",
    "@playwright/mcp@latest",
)


def npx_available() -> tuple[bool, str]:
    """Return ``(ok, detail)`` — whether the ``npx`` launcher is on PATH."""
    path = shutil.which("npx")
    if path is None:
        return False, "npx not found on PATH"
    return True, path


def playwright_preflight_error() -> str | None:
    """Reason the ``playwright`` server can't be enabled, or ``None`` if it can.

    The server is launched via ``npx @playwright/mcp`` and therefore needs
    Node.js (``npx``) on PATH. Browser binaries are fetched by the package on
    first use.
    """
    ok, detail = npx_available()
    if ok:
        return None
    return (
        "Node.js is required to run the Playwright MCP server (launched via "
        f"npx @playwright/mcp), but it is unavailable ({detail}). Install "
        "Node.js so that `npx` is on PATH, then try again."
    )


# Built-in MCP servers registered on every manager. The chat/topics surface and
# the agents surface both attach these by name when their ``mcp_enabled`` toggle
# is on, so keep names/transports stable when editing.
BUILTIN_CATALOG: tuple[_BuiltinSpec, ...] = (
    # GitHub MCP — remote streamable-http. Auth header is resolved lazily so
    # adding a token after startup works without a restart.
    _BuiltinSpec(
        "github",
        "streamable_http",
        url="https://api.githubcopilot.com/mcp/",
        headers_provider=_github_headers,
    ),
    # WorkIQ MCP — local stdio launcher. The npm package handles its own
    # interactive auth on first run.
    _BuiltinSpec("workiq", "stdio", command=_WORKIQ_STDIO_COMMAND, args=_WORKIQ_STDIO_ARGS),
    # Playwright MCP — Microsoft's official ``@playwright/mcp`` via npx (like
    # workiq). Drives a real browser (Microsoft Edge by default, for corporate
    # SSO) with a persistent profile so an interactive Entra/SSO sign-in survives
    # across runs, letting the model reach authenticated pages (navigate, read
    # text/DOM, screenshot). ``--browser`` and an optional ``--user-data-dir`` are
    # appended in ``_register_builtins``. Enable-time npx availability is checked
    # in the connect router.
    _BuiltinSpec(
        "playwright",
        "stdio",
        command=_PLAYWRIGHT_STDIO_COMMAND,
        args=_PLAYWRIGHT_STDIO_ARGS,
    ),
    # Fetch MCP — in-tree stdio subprocess exposing curl-like HTTP tools
    # (http_get / http_request). Uses the same interpreter that runs the backend
    # so the package is always importable.
    _BuiltinSpec(
        "fetch",
        "stdio",
        command=sys.executable,
        args=("-m", "precursor.backend.services.mcp.fetch_server"),
    ),
    # Workspace filesystem MCP — in-tree stdio subprocess exposing sandboxed
    # read/write tools over Workspace working trees. Needs the app's DB + config
    # to resolve a workspace to its on-disk path, so forward the environment.
    _BuiltinSpec(
        "workspace-fs",
        "stdio",
        command=sys.executable,
        args=("-m", "precursor.backend.services.mcp.workspace_fs_server"),
        forward_env=True,
    ),
    # Command runner MCP — in-tree stdio subprocess that runs bash/python/node
    # either inside a Docker "jail" (default) or directly on the host. Enable-time
    # Docker availability is checked in the connect router.
    _BuiltinSpec(
        "cmd-runner",
        "stdio",
        command=sys.executable,
        args=("-m", "precursor.backend.services.mcp.cmd_runner_server"),
        forward_env=True,
    ),
    # Precursor MCP — in-tree stdio subprocess exposing Precursor's *own*
    # capabilities (topics, messages, search, skills, memory, post_message,
    # schedules) outbound. Every tool is gated by a per-section mcp_expose toggle
    # read from the DB at call time, so nothing is served until the user opts in.
    _BuiltinSpec(
        "precursor",
        "stdio",
        command=sys.executable,
        args=("-m", "precursor.backend.services.mcp.precursor_server"),
        forward_env=True,
    ),
)


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
        # Whether the built-in ``workiq`` entry is in preview mode (hosted HTTP +
        # OAuth + writes) rather than the default local stdio launcher.
        self._workiq_preview: bool = False
        # Turns that paused on an interactive sign-in park an event here; the
        # re-authenticate endpoint sets them so the paused turn wakes and
        # retries acquiring its tools instead of answering without them.
        self._auth_waiters: set[asyncio.Event] = set()
        self._register_builtins()

    def _register_builtins(self) -> None:
        for spec in BUILTIN_CATALOG:
            args = list(spec.args)
            if spec.name == "playwright":
                settings = get_settings()
                # Browser channel. Default ``msedge`` so the server drives
                # Microsoft Edge and can ride the corporate Edge SSO/WAM broker —
                # the way authenticated Entra scraping actually works on a managed
                # machine. Override to ``chromium`` where Edge isn't installed.
                channel = (settings.playwright_browser or "msedge").strip()
                args += ["--browser", channel]
                # Only pin ``--user-data-dir`` when an override is set. Left empty
                # (default), ``@playwright/mcp`` uses its own shared machine-wide
                # profile, reusing any sign-in already onboarded there (incl. via
                # other Playwright-MCP tools) instead of forcing a fresh sign-in.
                override = settings.playwright_profile_dir.strip()
                if override:
                    os.makedirs(override, exist_ok=True)
                    args += ["--user-data-dir", override]
            self._servers[spec.name] = MCPServerEntry(
                name=spec.name,
                transport=spec.transport,
                url=spec.url,
                command=spec.command,
                args=args,
                env=dict(os.environ) if spec.forward_env else None,
                headers_provider=spec.headers_provider,
                builtin=True,
            )

    def get(self, name: str) -> MCPServerEntry | None:
        return self._servers.get(name)

    def list_entries(self) -> list[MCPServerEntry]:
        return list(self._servers.values())

    def auth_blocked_servers(self, names: list[str]) -> list[str]:
        """Subset of ``names`` currently parked in the ``needs_auth`` state."""
        blocked: list[str] = []
        for name in names:
            entry = self._servers.get(name)
            if entry is not None and entry.state == "needs_auth":
                blocked.append(name)
        return blocked

    def signal_auth_resolved(self) -> None:
        """Wake any turns paused waiting for an interactive MCP sign-in."""
        for event in list(self._auth_waiters):
            event.set()

    async def wait_for_auth(self, timeout: float) -> None:
        """Block until :meth:`signal_auth_resolved` fires or ``timeout`` elapses.

        Used by a paused chat turn so it retries acquiring its tools promptly
        once the user finishes the browser sign-in, rather than busy-polling.
        """
        event = asyncio.Event()
        self._auth_waiters.add(event)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            pass
        finally:
            self._auth_waiters.discard(event)

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

    @property
    def workiq_preview(self) -> bool:
        return self._workiq_preview

    def configure_workiq_preview(self, enabled: bool, *, auth_provider: httpx.Auth | None) -> None:
        """Switch the built-in ``workiq`` entry between stdio and hosted HTTP.

        Preview mode points WorkIQ at the OAuth-protected hosted endpoint (full
        read+write surface); off reverts to the local ``npx`` stdio launcher.
        Mutates the entry in place and resets its transient state so the next
        probe reflects the new transport.
        """
        from precursor.backend.services.mcp.workiq_preview import WORKIQ_PREVIEW_URL

        entry = self._servers.get("workiq")
        if entry is None:
            return
        self._workiq_preview = enabled
        if enabled:
            entry.transport = "streamable_http"
            entry.url = WORKIQ_PREVIEW_URL
            entry.command = None
            entry.args = []
            entry.auth_provider = auth_provider
        else:
            entry.transport = "stdio"
            entry.url = None
            entry.command = _WORKIQ_STDIO_COMMAND
            entry.args = list(_WORKIQ_STDIO_ARGS)
            entry.auth_provider = None
        entry.state = "disconnected"
        entry.error = None
        entry.tools = []

    async def retire_worker(self, name: str) -> None:
        """Close + drop any warm worker for ``name`` (e.g. after reconfiguring)."""
        async with self._pool_lock:
            worker = self._workers.pop(name, None)
        if worker is not None:
            await worker.aclose()

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
                if entry.headers_provider and headers is None and entry.auth_provider is None:
                    raise RuntimeError(
                        f"MCP server '{name}' has no credentials; configure them in Settings"
                    )
                async with (
                    streamablehttp_client(entry.url, headers=headers, auth=entry.auth_provider) as (
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
            # A WorkIQ preview connect that needs an interactive sign-in is a
            # distinct, recoverable state (surface a "Re-authenticate" prompt)
            # rather than a generic transport failure. The error may arrive
            # wrapped in an anyio ExceptionGroup, so unwrap to find it.
            from precursor.backend.services.mcp.workiq_preview import WorkIQAuthRequiredError

            auth_exc = _find_in_exception(exc, WorkIQAuthRequiredError)
            if auth_exc is not None:
                entry.state = "needs_auth"
                entry.error = str(auth_exc)
            else:
                entry.state = "error"
                entry.error = str(exc)
            logger.warning("MCP session for %s failed: %s", name, entry.error)
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
            # Preview toggle is workiq-specific; None means "not applicable" so
            # the UI only renders the extra checkbox for that server.
            "preview": self._workiq_preview if entry.name == "workiq" else None,
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
