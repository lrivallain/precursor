"""Durable tool catalogue for MCP servers.

The in-memory ``MCPServerEntry.tools`` list is populated by a successful connect
and lost on restart, which is why the Settings panel and the first chat prompt
both used to pay connect + initialize + list_tools for every enabled server
before they could show (or advertise) a single tool.

This module persists that catalogue in ``mcp_tool_cache`` so it is available
*before* anything connects:

* :func:`remember` refreshes a server's row after a successful connect.
* :func:`hydrate_entries` loads every row into the manager at startup, marking
  the entries ``tools_from_cache`` so nothing mistakes a cached catalogue for a
  live session.

The cache is a hint, never the authority: a live session decides what a call
actually reaches (see ``ActiveTools.call_tool``, which refreshes and retries once
when a cached tool turns out to be gone).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from precursor.backend.services.mcp.client import MCPToolDef

logger = logging.getLogger(__name__)


def _encode(tools: list[MCPToolDef]) -> str:
    return json.dumps(
        [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ],
        sort_keys=True,
    )


def _decode(server: str, raw: str) -> list[MCPToolDef]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    tools: list[MCPToolDef] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        schema: Any = item.get("input_schema")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        description = item.get("description")
        tools.append(
            MCPToolDef(
                server=server,
                name=name,
                description=description if isinstance(description, str) else "",
                input_schema=schema,
            )
        )
    return tools


async def remember(server: str, tools: list[MCPToolDef]) -> None:
    """Persist ``server``'s freshly listed tools. Best-effort — never raises.

    Called from the connect path, so a database that is unavailable (or a unit
    test with no schema) must degrade to "no cache" rather than break the
    connection that just succeeded.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import MCPToolCache

    encoded = _encode(tools)
    try:
        async with SessionLocal() as session:
            row = await session.get(MCPToolCache, server)
            if row is None:
                session.add(MCPToolCache(server=server, tools_json=encoded))
            elif row.tools_json == encoded:
                # Unchanged catalogue: skip the write so a warm-up sweep or a
                # reconnect storm doesn't churn the row on every connect.
                return
            else:
                row.tools_json = encoded
            await session.commit()
    except Exception:  # pragma: no cover - defensive: caching must never break a connect
        logger.debug("Could not cache the tool catalogue for %s", server, exc_info=True)


async def load() -> dict[str, list[MCPToolDef]]:
    """Every cached catalogue, keyed by server name. Best-effort — never raises."""
    from sqlalchemy import select

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import MCPToolCache

    try:
        async with SessionLocal() as session:
            rows = (await session.execute(select(MCPToolCache))).scalars().all()
    except Exception:  # pragma: no cover - defensive: a missing table means "no cache"
        logger.debug("Could not read the MCP tool cache", exc_info=True)
        return {}
    return {row.server: _decode(row.server, row.tools_json) for row in rows}


async def forget(server: str) -> None:
    """Drop ``server``'s cached catalogue (e.g. when the entry is deleted)."""
    from sqlalchemy import delete

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import MCPToolCache

    try:
        async with SessionLocal() as session:
            await session.execute(delete(MCPToolCache).where(MCPToolCache.server == server))
            await session.commit()
    except Exception:  # pragma: no cover - defensive
        logger.debug("Could not drop the cached tool catalogue for %s", server, exc_info=True)


async def hydrate_entries() -> int:
    """Seed the manager's entries from the cache. Returns how many were hydrated.

    Only fills entries that have no live catalogue yet, so a server that already
    connected in this process keeps its authoritative list.
    """
    from precursor.backend.services.mcp.client import get_mcp_client_manager

    cached = await load()
    if not cached:
        return 0
    manager = get_mcp_client_manager()
    hydrated = 0
    for entry in manager.list_entries():
        tools = cached.get(entry.name)
        if not tools or entry.tools:
            continue
        entry.tools = tools
        entry.tools_from_cache = True
        hydrated += 1
    if hydrated:
        logger.info("Hydrated %d MCP tool catalogue(s) from the cache", hydrated)
    return hydrated
