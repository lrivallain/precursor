"""conversation approval policies and parked tool approvals

Adds a per-topic / per-chat ``approval_policy`` override (``NULL`` inherits the
global ``conversation_approval_policy``) and the ``tool_approvals`` table that
persists tool calls waiting on the user, so a pending approval survives a reload.

Revision ID: c7d41e8a9f20
Revises: b5e19c7a3d42
Create Date: 2026-10-06 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d41e8a9f20"
down_revision: str | None = "b5e19c7a3d42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("topics", sa.Column("approval_policy", sa.String(length=16), nullable=True))
    op.add_column("chats", sa.Column("approval_policy", sa.String(length=16), nullable=True))
    op.create_table(
        "tool_approvals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("chat_id", sa.Integer(), nullable=True),
        sa.Column("assistant_message_id", sa.Integer(), nullable=True),
        sa.Column("tool_call_id", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=255), nullable=False),
        sa.Column("server", sa.String(length=128), nullable=False),
        sa.Column("raw_tool", sa.String(length=255), nullable=False),
        sa.Column("arguments", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed", sa.Boolean(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(topic_id IS NOT NULL AND chat_id IS NULL) OR (topic_id IS NULL AND chat_id IS NOT NULL)",
            name="ck_tool_approval_container",
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tool_approvals_topic_id", "tool_approvals", ["topic_id"])
    op.create_index("ix_tool_approvals_chat_id", "tool_approvals", ["chat_id"])
    op.create_index(
        "ix_tool_approvals_assistant_message_id", "tool_approvals", ["assistant_message_id"]
    )
    op.create_index("ix_tool_approvals_status", "tool_approvals", ["status"])


def downgrade() -> None:
    op.drop_index("ix_tool_approvals_status", table_name="tool_approvals")
    op.drop_index("ix_tool_approvals_assistant_message_id", table_name="tool_approvals")
    op.drop_index("ix_tool_approvals_chat_id", table_name="tool_approvals")
    op.drop_index("ix_tool_approvals_topic_id", table_name="tool_approvals")
    op.drop_table("tool_approvals")
    op.drop_column("chats", "approval_policy")
    op.drop_column("topics", "approval_policy")
