"""User-defined MCP server entry."""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, TimestampMixin


class MCPServer(Base, TimestampMixin):
    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Logical name used as the catalog key and slash-namespacing prefix.
    # Must be unique across built-ins and user-defined entries.
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # "streamable_http" or "stdio".
    transport: Mapped[str] = mapped_column(String(20), nullable=False)
    # streamable_http only.
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # stdio only.
    command: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # JSON-encoded list[str] for stdio args.
    args_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # JSON-encoded dict[str, str] of HTTP headers (e.g. Authorization).
    headers_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
