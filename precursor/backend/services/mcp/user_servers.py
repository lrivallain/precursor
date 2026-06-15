"""DB-backed CRUD + manager hydration for user-defined MCP servers."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import MCPServer
from precursor.backend.services.mcp.client import (
    MCPClientManager,
    get_mcp_client_manager,
)

logger = logging.getLogger(__name__)


def _decode_args(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


def _decode_headers(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(v, dict):
        return {}
    return {str(k): str(x) for k, x in v.items()}


def apply_to_manager(row: MCPServer, manager: MCPClientManager | None = None) -> None:
    """Register (or replace) a user MCP server in the in-memory manager."""
    manager = manager or get_mcp_client_manager()
    manager.register_user_entry(
        name=row.name,
        transport=row.transport,  # type: ignore[arg-type]
        url=row.url,
        command=row.command,
        args=_decode_args(row.args_json),
        headers=_decode_headers(row.headers_json) or None,
    )


async def hydrate_user_entries() -> None:
    """Load every user-defined MCP server into the manager. Idempotent."""
    manager = get_mcp_client_manager()
    async with SessionLocal() as session:
        rows = (await session.execute(select(MCPServer))).scalars().all()
    for row in rows:
        try:
            apply_to_manager(row, manager)
        except ValueError as exc:
            logger.warning("Skipping user MCP server '%s': %s", row.name, exc)


def to_public_dict(row: MCPServer) -> dict[str, Any]:
    """JSON-safe view of a stored entry; redacts header values."""
    header_keys = sorted(_decode_headers(row.headers_json).keys())
    return {
        "id": row.id,
        "name": row.name,
        "transport": row.transport,
        "url": row.url,
        "command": row.command,
        "args": _decode_args(row.args_json),
        "header_keys": header_keys,
    }


async def get_row_by_name(session: AsyncSession, name: str) -> MCPServer | None:
    result = await session.execute(select(MCPServer).where(MCPServer.name == name))
    return result.scalar_one_or_none()
