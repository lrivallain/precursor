"""fold the workspace-fs MCP server into precursor

The ``workspace-fs`` built-in is gone: its file tools now live in the
``precursor`` server behind the ``workspaces`` / ``workspaces_write`` capability
sections. An install that had it switched on keeps the same reach by turning on
``precursor`` and both sections; the stale ``workspace-fs`` key is dropped.

Revision ID: a7d2c91e4b35
Revises: 5ec9892c32b6
Create Date: 2026-10-06 00:00:00.000000
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "a7d2c91e4b35"
down_revision = "5ec9892c32b6"
branch_labels = None
depends_on = None


def _load(conn: sa.Connection, key: str) -> dict[str, object] | None:
    raw = conn.execute(
        sa.text("SELECT value FROM app_settings WHERE key = :k"), {"k": key}
    ).scalar()
    if raw is None:
        return None
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _store(conn: sa.Connection, key: str, value: dict[str, object]) -> None:
    payload = json.dumps(value)
    updated = conn.execute(
        sa.text("UPDATE app_settings SET value = :v WHERE key = :k"), {"v": payload, "k": key}
    ).rowcount
    if not updated:
        conn.execute(
            sa.text("INSERT INTO app_settings (key, value) VALUES (:k, :v)"),
            {"k": key, "v": payload},
        )


def upgrade() -> None:
    conn = op.get_bind()
    enabled = _load(conn, "mcp_enabled")
    if enabled is None or "workspace-fs" not in enabled:
        return
    was_on = bool(enabled.pop("workspace-fs"))
    if was_on:
        enabled["precursor"] = True
        expose = _load(conn, "mcp_expose") or {}
        expose["workspaces"] = True
        expose["workspaces_write"] = True
        _store(conn, "mcp_expose", expose)
    _store(conn, "mcp_enabled", enabled)


def downgrade() -> None:
    # Not reversible: the folded sections can't be told apart from ones the user
    # switched on themselves.
    pass
