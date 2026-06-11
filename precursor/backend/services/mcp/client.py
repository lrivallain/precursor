"""Inbound MCP — Precursor as a client to external tool servers.

Holds a registry of configured MCP servers (defined in app settings) and tracks
their connection state. The actual transport handshake is implemented behind
``connect`` / ``disconnect``; this scaffold keeps an in-memory status registry
so the UI has something to render today.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

ConnectionState = Literal["disconnected", "connecting", "connected", "error"]


@dataclass(slots=True)
class MCPServerStatus:
    name: str
    transport: str = "stdio"
    command: str | None = None
    url: str | None = None
    state: ConnectionState = "disconnected"
    error: str | None = None
    tools: list[str] = field(default_factory=list)


class MCPClientManager:
    def __init__(self) -> None:
        self._servers: dict[str, MCPServerStatus] = {}

    def register(self, name: str, *, transport: str = "stdio", **kwargs: Any) -> MCPServerStatus:
        status = MCPServerStatus(name=name, transport=transport, **kwargs)
        self._servers[name] = status
        return status

    def list_servers(self) -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "transport": s.transport,
                "command": s.command,
                "url": s.url,
                "state": s.state,
                "error": s.error,
                "tools": s.tools,
            }
            for s in self._servers.values()
        ]

    async def connect(self, name: str) -> dict[str, Any]:
        status = self._servers.get(name)
        if status is None:
            # Auto-register with defaults so the UI doesn't 404 on first use.
            status = self.register(name)
        status.state = "connected"  # TODO: wire real MCP transport handshake
        status.error = None
        return {"name": status.name, "state": status.state, "tools": status.tools}

    async def disconnect(self, name: str) -> dict[str, Any]:
        status = self._servers.get(name)
        if status is None:
            return {"name": name, "state": "disconnected"}
        status.state = "disconnected"
        return {"name": status.name, "state": status.state}


@lru_cache
def get_mcp_client_manager() -> MCPClientManager:
    return MCPClientManager()
