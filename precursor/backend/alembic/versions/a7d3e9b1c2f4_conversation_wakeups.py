"""conversation wakeups

A model can pause a topic, chat or agent and have it woken up later with a
prompt of its own (``models/wakeup.py``).

Revision ID: a7d3e9b1c2f4
Revises: c5e2a7f19d40
Create Date: 2026-11-25 10:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7d3e9b1c2f4"
down_revision = "c5e2a7f19d40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wakeups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("chat_id", sa.Integer(), nullable=True),
        sa.Column("agent_session_id", sa.Integer(), nullable=True),
        sa.Column("wake_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "(CASE WHEN topic_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN chat_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN agent_session_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_wakeup_target",
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_session_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_id"),
        sa.UniqueConstraint("chat_id"),
        sa.UniqueConstraint("agent_session_id"),
    )
    op.create_index(op.f("ix_wakeups_wake_at"), "wakeups", ["wake_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_wakeups_wake_at"), table_name="wakeups")
    op.drop_table("wakeups")
