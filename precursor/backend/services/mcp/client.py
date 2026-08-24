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
from collections.abc import AsyncIterator, Callable, Iterable
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
    # False only for user-defined (DB-backed, editable) entries. A plugin's
    # servers are ``True`` — the user can enable/disable them but not edit them.
    builtin: bool = True
    # Id of the plugin that contributed this entry, for provenance in the UI.
    # ``None`` for core's own built-ins and for user-defined servers.
    plugin_id: str | None = None
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


def build_playwright_args(
    base_args: Iterable[str],
    *,
    browser: str,
    profile_dir: str,
    supports_browser_flag: bool = True,
) -> list[str]:
    """Assemble the ``npx @playwright/mcp`` argv from the resolved browser + profile.

    ``browser`` == ``"default"`` (or blank) omits ``--browser`` entirely so the
    installed ``@playwright/mcp`` picks its own default. That's the explicit
    escape hatch for a build that predates the flag and rejects it with "unknown
    option" (e.g. a stale registry mirror), which otherwise stops the server from
    starting at all.

    ``supports_browser_flag=False`` drops ``--browser`` for *any* channel — the
    automatic counterpart, set from :func:`playwright_supports_browser_flag` once
    we've confirmed the resolved binary can't accept the flag, so the default
    ``msedge`` still starts on those builds instead of failing outright.
    """
    args = list(base_args)
    channel = (browser or "").strip()
    if supports_browser_flag and channel and channel != "default":
        args += ["--browser", channel]
    override = (profile_dir or "").strip()
    if override:
        os.makedirs(override, exist_ok=True)
        args += ["--user-data-dir", override]
    return args


def npx_available() -> tuple[bool, str]:
    """Return ``(ok, detail)`` — whether the ``npx`` launcher is on PATH."""
    path = shutil.which("npx")
    if path is None:
        return False, "npx not found on PATH"
    return True, path


# Cache for the one-shot ``--browser`` capability probe (see below). ``None``
# means "not probed yet"; a bool is the resolved answer for this process.
_playwright_browser_flag_support: bool | None = None


async def playwright_supports_browser_flag() -> bool:
    """Whether the resolved ``@playwright/mcp`` accepts ``--browser``. Cached.

    Some builds predate the flag — notably the ``1.52`` alpha a stale registry
    mirror can pin as ``@latest`` — and abort startup with
    ``error: unknown option '--browser'``. We probe ``--help`` once and cache the
    result so we never hand the launcher an argument it will reject.

    On any probe failure (npx missing, timeout, non-zero exit) we assume the flag
    *is* supported and return ``True``: that preserves the status-quo behaviour
    and the SSO-friendly ``msedge`` default. We only report ``False`` when
    ``--help`` ran and positively lacked ``--browser``, so the flag is dropped
    solely for builds we've confirmed can't accept it.
    """
    global _playwright_browser_flag_support
    if _playwright_browser_flag_support is not None:
        return _playwright_browser_flag_support
    ok, _ = npx_available()
    if not ok:
        return True
    supported = True
    try:
        proc = await asyncio.create_subprocess_exec(
            _PLAYWRIGHT_STDIO_COMMAND,
            *_PLAYWRIGHT_STDIO_ARGS,
            "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            return True
        if proc.returncode == 0:
            supported = "--browser" in stdout.decode("utf-8", "replace")
    except Exception:  # pragma: no cover - defensive: treat probe faults as "supported"
        return True
    _playwright_browser_flag_support = supported
    if not supported:
        logger.info(
            "Resolved @playwright/mcp does not support --browser; launching with "
            "its default browser (the channel selection is ignored on this build)."
        )
    return supported


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
    # Microsoft Agent 365 MCP — hosted, per-tenant streamable-HTTP endpoints
    # behind Entra OAuth. The URL embeds a tenant GUID that isn't known until the
    # setting is read (or discovered from a WorkIQ token), so both entries start
    # url-less and are pointed at the tenant by ``configure_agent365`` on startup
    # and whenever the tenant setting changes.
    _BuiltinSpec("workiq-teams", "streamable_http"),
    _BuiltinSpec("workiq-user", "streamable_http"),
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
    # draw.io MCP — in-tree stdio subprocess that authors native ``.drawio``
    # files (mxGraph XML) into a Workspace working tree, with server-side
    # layered layout so the model describes a graph instead of guessing
    # coordinates. Shares workspace-fs's sandbox, so it needs the app's DB +
    # config to resolve a workspace to its on-disk path.
    _BuiltinSpec(
        "drawio",
        "stdio",
        command=sys.executable,
        args=("-m", "precursor.backend.services.mcp.drawio_server"),
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
        # Credentials known to need an interactive sign-in *right now*, keyed by
        # ``credential_key`` so one dead token flags every server sharing it. A
        # flagged OAuth connect is short-circuited to ``needs_auth`` in
        # ``_open_transport`` instead of paying the full OAuth discovery+refresh
        # handshake against a token that can only fail — that handshake latency is
        # what made the first request after a lapse stall for seconds. Set by the
        # keep-alive (proactively) and by a failed connect; cleared when a fresh
        # provider is installed or a sign-in resolves.
        self._auth_short_circuit: set[str] = set()
        self._register_builtins()

    def _register_builtins(self) -> None:
        for spec in BUILTIN_CATALOG:
            args = list(spec.args)
            if spec.name == "playwright":
                settings = get_settings()
                # Browser channel. Default ``msedge`` so the server drives
                # Microsoft Edge and can ride the corporate Edge SSO/WAM broker —
                # the way authenticated Entra scraping actually works on a managed
                # machine. Override to ``chromium`` where Edge isn't installed, or
                # ``default`` where the resolved ``@playwright/mcp`` predates
                # ``--browser``. A DB override applied at startup can replace this
                # env-derived default (see ``configure_playwright``).
                channel = (settings.playwright_browser or "msedge").strip()
                # ``--user-data-dir`` is only pinned when an override is set. Left
                # empty (default), ``@playwright/mcp`` uses its own shared
                # machine-wide profile, reusing any sign-in already onboarded
                # there instead of forcing a fresh one.
                args = build_playwright_args(
                    args,
                    browser=channel,
                    profile_dir=settings.playwright_profile_dir,
                )
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
        """Subset of ``names`` currently parked in the ``needs_auth`` state,
        collapsed to one representative per credential.

        Callers turn each returned name into a user-facing sign-in prompt. The
        Agent 365 servers share a single Entra token, so returning both would
        raise two prompts for one sign-in the user can only answer once — the
        exact noise that makes multi-server WorkIQ feel relentless. Collapsing
        here fixes every caller (turn engine, workspaces, guards) at once.
        """
        from precursor.backend.services.mcp.oauth_registry import collapse_by_credential

        blocked: list[str] = []
        for name in names:
            entry = self._servers.get(name)
            if entry is not None and entry.state == "needs_auth":
                blocked.append(name)
        return collapse_by_credential(blocked)

    def signal_auth_resolved(self) -> None:
        """Wake any turns paused waiting for an interactive MCP sign-in."""
        # A sign-in completed somewhere; drop every short-circuit verdict so the
        # next connect re-verifies against the (hopefully) fresh token rather than
        # staying pinned to ``needs_auth``. Over-broad but self-healing: a token
        # that is still dead simply re-flags itself on its next failed connect.
        self._auth_short_circuit.clear()
        for event in list(self._auth_waiters):
            event.set()

    def mark_auth_required(self, name: str, *, message: str | None = None) -> None:
        """Record that ``name``'s credential needs an interactive sign-in.

        Lets a later connect for the same credential surface ``needs_auth``
        immediately (see ``_open_transport``) instead of re-discovering it the
        slow way. Also flips the entry to ``needs_auth`` so the Settings UI and
        ``auth_blocked_servers`` reflect the lapse even before any connect.
        """
        from precursor.backend.services.mcp import auth_trace
        from precursor.backend.services.mcp.oauth_registry import credential_key, is_oauth_server

        key = credential_key(name)
        newly = key not in self._auth_short_circuit
        self._auth_short_circuit.add(key)
        entry = self._servers.get(name)
        if entry is not None:
            entry.state = "needs_auth"
            if message:
                entry.error = message
        if newly and is_oauth_server(name):
            auth_trace.record(name, "credential flagged as needing a sign-in", message=message)

    def clear_auth_required(self, name: str) -> None:
        """Forget a prior ``mark_auth_required`` for ``name``'s credential.

        Called when a fresh provider is installed or a silent refresh succeeds,
        so a subsequent connect is allowed to proceed for real.
        """
        from precursor.backend.services.mcp import auth_trace
        from precursor.backend.services.mcp.oauth_registry import credential_key, is_oauth_server

        key = credential_key(name)
        if key in self._auth_short_circuit and is_oauth_server(name):
            auth_trace.record(
                name, "credential no longer flagged as needing a sign-in", level=logging.DEBUG
            )
        self._auth_short_circuit.discard(key)

    def _auth_short_circuited(self, name: str) -> bool:
        from precursor.backend.services.mcp.oauth_registry import credential_key

        return credential_key(name) in self._auth_short_circuit

    def is_auth_short_circuited(self, name: str) -> bool:
        """Whether connects for ``name``'s credential are being fast-failed.

        Public read of the same verdict ``_open_transport`` acts on, for the auth
        diagnostics endpoint: "the server says ``needs_auth``" and "the manager
        will refuse to even try" are different facts, and a disagreement between
        them is itself a bug worth seeing.
        """
        return self._auth_short_circuited(name)

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

    def register_plugin_entry(
        self,
        *,
        plugin_id: str,
        name: str,
        transport: Literal["streamable_http", "stdio"],
        url: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        headers: dict[str, str] | None = None,
        forward_env: bool = False,
    ) -> MCPServerEntry:
        """Register a server contributed by a plugin.

        Plugin entries join the same catalogue as the built-ins, so they inherit
        the existing per-surface toggles, probing and tool plumbing for free.
        They are marked ``builtin`` because they are not user-editable rows, and
        carry ``plugin_id`` so the UI can attribute them.

        Names are namespaced by the caller (see ``PluginRegistry.add_mcp_server``)
        so two plugins can't collide, and a core built-in always wins.
        """
        existing = self._servers.get(name)
        if existing is not None and existing.plugin_id is None:
            raise ValueError(f"'{name}' is already registered by core or a user server")
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
            # Plugins ship in the app's own environment, so an in-process stdio
            # server needs the same DB/config access the in-tree ones get.
            env=dict(os.environ) if forward_env else None,
            headers_provider=provider,
            builtin=True,
            plugin_id=plugin_id,
        )
        self._servers[name] = entry
        return entry

    def plugin_entry_names(self) -> set[str]:
        return {name for name, e in self._servers.items() if e.plugin_id is not None}

    def unregister_plugin_entry(self, name: str) -> bool:
        entry = self._servers.get(name)
        if entry is None or entry.plugin_id is None:
            return False
        del self._servers[name]
        return True

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
        # A fresh provider (or a revert to stdio) means "signable now" — drop any
        # short-circuit verdict so the reauth probe actually connects.
        self.clear_auth_required("workiq")

    def configure_agent365(
        self, name: str, *, url: str | None, auth_provider: httpx.Auth | None
    ) -> None:
        """Point a built-in Agent 365 entry at the resolved per-tenant endpoint.

        ``url=None`` means no tenant could be resolved, so the entry is left
        addressable-but-broken with a clear error instead of silently pointing at
        an invalid URL (Entra 400s on a non-GUID tenant segment).
        """
        from precursor.backend.services.mcp.agent365 import TENANT_REQUIRED_MESSAGE

        entry = self._servers.get(name)
        if entry is None:
            return
        entry.url = url
        entry.auth_provider = auth_provider
        entry.state = "disconnected"
        entry.error = None if url else TENANT_REQUIRED_MESSAGE
        entry.tools = []
        # Installing a fresh provider means the credential is signable again —
        # drop any short-circuit verdict so the reauth probe connects for real
        # instead of being fast-failed back to ``needs_auth``.
        self.clear_auth_required(name)

    def configure_playwright(
        self, *, browser: str, profile_dir: str, supports_browser_flag: bool = True
    ) -> None:
        """Rebuild the built-in ``playwright`` entry's argv for a browser channel.

        Called on startup and whenever the ``playwright_browser`` setting changes
        so a DB override replaces the env-derived default. Mutates the entry in
        place and resets its transient state; the caller retires any warm worker
        so the next probe relaunches ``npx`` with the new args.
        ``supports_browser_flag=False`` drops ``--browser`` regardless of channel
        for builds that can't accept it.
        """
        entry = self._servers.get("playwright")
        if entry is None:
            return
        entry.args = build_playwright_args(
            _PLAYWRIGHT_STDIO_ARGS,
            browser=browser,
            profile_dir=profile_dir,
            supports_browser_flag=supports_browser_flag,
        )
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

        # Fast-fail a connect we already know needs an interactive sign-in. The
        # non-interactive OAuth provider would reach the same verdict, but only
        # after the streamable-HTTP transport runs the full OAuth discovery +
        # refresh handshake against a dead token — several seconds the user waits
        # before the re-authenticate banner appears. The keep-alive (and any
        # prior failed connect) records the verdict, so surface ``needs_auth`` at
        # once instead. Gated on an OAuth server with a provider so plain servers
        # are never affected.
        if entry.auth_provider is not None and self._auth_short_circuited(name):
            from precursor.backend.services.mcp import auth_trace
            from precursor.backend.services.mcp.oauth_registry import is_oauth_server
            from precursor.backend.services.mcp.workiq_preview import WorkIQAuthRequiredError

            if is_oauth_server(name):
                entry.state = "needs_auth"
                message = entry.error or "Sign-in required to use this server."
                entry.error = message
                auth_trace.record(
                    name,
                    "connect fast-failed on the known-dead credential",
                    level=logging.DEBUG,
                )
                raise WorkIQAuthRequiredError(message)

        entry.state = "connecting"
        entry.error = None
        try:
            if entry.transport == "streamable_http":
                if not entry.url:
                    from precursor.backend.services.mcp.agent365 import (
                        TENANT_REQUIRED_MESSAGE,
                        is_agent365_server,
                    )

                    raise RuntimeError(
                        TENANT_REQUIRED_MESSAGE
                        if is_agent365_server(name)
                        else f"MCP server '{name}' has no URL configured"
                    )
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
                # Record the verdict so the *next* connect for this credential
                # short-circuits instantly instead of stalling on the same doomed
                # handshake — this is what makes the second request after a lapse
                # fast even when the keep-alive is disabled.
                from precursor.backend.services.mcp import auth_trace

                auth_trace.begin_episode(name, "a live connect found the credential dead")
                self.mark_auth_required(name)
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
        from precursor.backend.services.mcp.agent365 import is_agent365_server

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
            # Which plugin contributed this server, or None for core/user ones.
            "plugin_id": entry.plugin_id,
            "enabled": enabled,
            # Preview toggle is workiq-specific; None means "not applicable" so
            # the UI only renders the extra checkbox for that server.
            "preview": self._workiq_preview if entry.name == "workiq" else None,
            # Whether this server authenticates through Precursor's browser
            # OAuth flow, so the UI knows to offer the sign-in / re-authenticate
            # action (hosted WorkIQ preview + the Agent 365 servers).
            "oauth": (entry.name == "workiq" and self._workiq_preview)
            or is_agent365_server(entry.name),
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
        # Mark before the call, not after: a failing tool still proves the user
        # is actively using this server, and that's what keeps its credential
        # eligible for background refresh.
        from precursor.backend.services.mcp.usage import mark_server_used

        mark_server_used(server)
        return await worker.call(raw_name, args)

    async def aclose(self) -> None:
        """Close the workers backing this bundle (only used when ephemeral)."""
        workers = list(self.workers.values())
        if workers:
            await asyncio.gather(*(w.aclose() for w in workers), return_exceptions=True)


@lru_cache
def get_mcp_client_manager() -> MCPClientManager:
    return MCPClientManager()


async def configure_playwright_server() -> None:
    """Apply the resolved ``playwright_browser`` setting to the built-in entry.

    Called on startup and whenever the setting changes: resolves the DB override
    (falling back to the env default) and rebuilds the ``playwright`` argv, then
    retires any warm worker so the change takes effect on the next probe.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.services.app_settings import resolve_playwright_browser

    manager = get_mcp_client_manager()
    async with SessionLocal() as session:
        browser = await resolve_playwright_browser(session)
    supports = await playwright_supports_browser_flag()
    manager.configure_playwright(
        browser=browser,
        profile_dir=get_settings().playwright_profile_dir,
        supports_browser_flag=supports,
    )
    await manager.retire_worker("playwright")
