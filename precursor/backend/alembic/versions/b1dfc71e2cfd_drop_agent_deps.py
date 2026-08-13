"""drop agent_deps

Agent-to-agent chaining moved to the Workflows feature; the per-agent DAG
in-edge table (``agent_deps``) is no longer used.

Revision ID: b1dfc71e2cfd
Revises: 59bb34f0c3f7
Create Date: 2026-08-12 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b1dfc71e2cfd"
down_revision = "59bb34f0c3f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_agent_deps_depends_on_id", table_name="agent_deps")
    op.drop_index("ix_agent_deps_agent_id", table_name="agent_deps")
    op.drop_table("agent_deps")


def downgrade() -> None:
    op.create_table(
        "agent_deps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("depends_on_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["depends_on_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "depends_on_id", name="uq_agent_dep_edge"),
    )
    op.create_index("ix_agent_deps_agent_id", "agent_deps", ["agent_id"])
    op.create_index("ix_agent_deps_depends_on_id", "agent_deps", ["depends_on_id"])
