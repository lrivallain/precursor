"""Inbound MCP — Precursor as a client to external tool servers.

Today we ship one built-in server (GitHub, remote streamable-http at
``https://api.githubcopilot.com/mcp``). The same machinery is designed to host
additional built-ins (work-iq) and user-defined BYO servers later. Sessions
are opened per chat turn rather than kept alive — the SDK's session objects
are async-context-bound and the request scope is the natural unit of work.
"""

from __future__ import annotations

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
        """Open a live MCP session against a configured server.

        Yields ``(session, tools)``; caller must use as ``async with``.
        ``github_token`` is the resolved token for servers that authenticate
        with it (e.g. the built-in GitHub server).
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
        "X-MCP-Toolsets": "all",
    }


@lru_cache
def get_mcp_client_manager() -> MCPClientManager:
    return MCPClientManager()
