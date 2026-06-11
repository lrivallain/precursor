"""HTTP-facing MCP endpoints.

The actual MCP protocol runs over its own transport; these endpoints expose a
small JSON surface used by the frontend Settings panel to introspect connected
MCP servers and to mount/unmount them per topic.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from precursor.backend.services.mcp.client import get_mcp_client_manager
from precursor.backend.services.mcp.server import get_mcp_server

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.get("/servers")
async def list_servers() -> list[dict[str, Any]]:
    """List MCP tool servers known to the client manager (configured + status)."""
    manager = get_mcp_client_manager()
    return manager.list_servers()


@router.get("/server/info")
async def server_info() -> dict[str, Any]:
    """Describe the MCP server *we* expose (so external tools know our capabilities)."""
    server = get_mcp_server()
    return server.describe()


@router.post("/servers/{name}/connect")
async def connect_server(name: str) -> dict[str, Any]:
    manager = get_mcp_client_manager()
    return await manager.connect(name)


@router.post("/servers/{name}/disconnect")
async def disconnect_server(name: str) -> dict[str, Any]:
    manager = get_mcp_client_manager()
    return await manager.disconnect(name)
