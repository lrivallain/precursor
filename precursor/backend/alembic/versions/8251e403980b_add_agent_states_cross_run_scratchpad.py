"""add agent_states cross-run scratchpad

Adds ``agent_states`` — an agent's private, durable key/value scratchpad that
**survives re-runs**, unlike ``agent_artifacts`` (wiped at the start of every
fresh run) and unlike ``memories`` (global and injected into every turn). It's
what a scheduled or webhook-triggered agent uses to remember a cursor, a set of
seen ids, or a counter between runs.

Revision ID: 8251e403980b
Revises: 4fd7f3a6c9e4
Create Date: 2026-08-13 17:14:41.625491

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8251e403980b"
down_revision: str | None = "4fd7f3a6c9e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "key", name="uq_agent_state_agent_key"),
    )
    op.create_index("ix_agent_states_agent_id", "agent_states", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_states_agent_id", table_name="agent_states")
    op.drop_table("agent_states")
