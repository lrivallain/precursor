"""Persisted tool catalogue for an MCP server.

``MCPServerEntry.tools`` is in-memory only, so every restart threw the catalogue
away and the first Settings render (and the first chat prompt) had to reconnect
every enabled server to learn what it exposes. This table survives the restart:
the entry is hydrated from it at startup and the row is refreshed after each
successful connect.

Kept out of ``AppSetting`` on purpose — ``routers/settings._load_all`` decodes
*every* settings row on each ``GET/PUT /api/settings``, and a dozen tool
catalogues (GitHub alone advertises ~94 schemas) is a lot of JSON to parse for a
request that never looks at them.
"""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, TimestampMixin


class MCPToolCache(Base, TimestampMixin):
    __tablename__ = "mcp_tool_cache"

    # Catalog key of the server (built-in, plugin or user-defined). One row per
    # server, so the name is the natural primary key.
    server: Mapped[str] = mapped_column(String(64), primary_key=True)
    # JSON-encoded list of ``{"name", "description", "input_schema"}``.
    tools_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
