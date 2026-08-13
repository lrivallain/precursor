"""add agent autonomy + mission fields

Turns an agent session from a one-shot turn machine into a persistent worker
pursuing an objective: ``autonomy_enabled`` opts the session into the goal loop,
``max_steps``/``step_count`` bound and track autonomous continuation,
``progress``/``progress_label`` carry the agent's self-reported mission progress
for the dashboard, and ``blocked_question`` holds the decision an agent raised
when it parks itself in the new ``blocked`` state (distinct from a tool-permission
gate). The objective itself reuses the existing durable ``task_prompt``.

Revision ID: f2a3c4d5e6b7
Revises: 8ae5cacca272
Create Date: 2026-07-15 10:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3c4d5e6b7"
down_revision: str | None = "8ae5cacca272"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column(
            "autonomy_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "agent_sessions",
        sa.Column("max_steps", sa.Integer(), nullable=False, server_default="12"),
    )
    op.add_column(
        "agent_sessions",
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "agent_sessions",
        sa.Column("progress", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_sessions",
        sa.Column("progress_label", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "agent_sessions",
        sa.Column("blocked_question", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "blocked_question")
    op.drop_column("agent_sessions", "progress_label")
    op.drop_column("agent_sessions", "progress")
    op.drop_column("agent_sessions", "step_count")
    op.drop_column("agent_sessions", "max_steps")
    op.drop_column("agent_sessions", "autonomy_enabled")
