"""add export_id to agents and workflows

Revision ID: 167f9c4e5752
Revises: d4b1c9e30f75
Create Date: 2026-08-13 10:37:33.150972

Adds the portable identity used by YAML export/import. Nullable and minted
lazily on first export, so existing rows need no backfill. The unique index is
safe on a nullable column: SQLite treats NULLs as distinct, so every un-exported
row coexists.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "167f9c4e5752"
down_revision: str | None = "d4b1c9e30f75"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_sessions", sa.Column("export_id", sa.String(length=64), nullable=True))
    op.create_index(
        op.f("ix_agent_sessions_export_id"), "agent_sessions", ["export_id"], unique=True
    )
    op.add_column("workflows", sa.Column("export_id", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_workflows_export_id"), "workflows", ["export_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_workflows_export_id"), table_name="workflows")
    op.drop_column("workflows", "export_id")
    op.drop_index(op.f("ix_agent_sessions_export_id"), table_name="agent_sessions")
    op.drop_column("agent_sessions", "export_id")
