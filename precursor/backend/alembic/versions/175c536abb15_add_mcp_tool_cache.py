"""add mcp_tool_cache

Persists each MCP server's tool catalogue so it survives a restart: the entries
are hydrated from this table at startup, which is what lets Settings render and
the first chat prompt advertise tools before anything connects.

Autogenerate also proposed re-creating four unnamed SQLite foreign keys that
already exist (a known batch-mode artifact); those were dropped by hand so this
revision only adds the new table.

Revision ID: 175c536abb15
Revises: e3b7a1c95d24
Create Date: 2026-09-01 21:28:52.302567

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "175c536abb15"
down_revision: str | None = "e3b7a1c95d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_tool_cache",
        sa.Column("server", sa.String(length=64), nullable=False),
        sa.Column("tools_json", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("server"),
    )


def downgrade() -> None:
    op.drop_table("mcp_tool_cache")
