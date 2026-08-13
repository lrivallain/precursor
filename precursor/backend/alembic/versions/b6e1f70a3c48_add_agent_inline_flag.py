"""agent inline flag (workflow inline steps)

Adds ``agent_sessions.inline`` — the agent is owned by a single workflow step
rather than being a reusable unit. The runtime is agent-keyed, so an ``inline``
step still needs *an* agent to drive; this flag keeps that vessel out of the
Agents list and marks it for deletion when its owning step goes away, so a
one-off task in a pipeline doesn't clutter the agent roster.

Revision ID: b6e1f70a3c48
Revises: a9d4e7f21c60
Create Date: 2026-08-21 17:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b6e1f70a3c48"
down_revision = "a9d4e7f21c60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("inline", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "inline")
