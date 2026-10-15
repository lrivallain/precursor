"""approval rules

Adds the ``approval_rules`` table: standing allow / ask answers that sit in front
of a conversation's approval policy. "Always allow here" answers already given on
approval cards become conversation-scoped allow rules.

Revision ID: d2a96f4b1c58
Revises: c7d41e8a9f20
Create Date: 2026-10-15 09:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2a96f4b1c58"
down_revision: str | None = "c7d41e8a9f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("effect", sa.String(length=8), nullable=False),
        sa.Column("server", sa.String(length=128), nullable=False),
        sa.Column("tool", sa.String(length=255), nullable=False),
        sa.Column("arg_match", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("chat_id", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("hit_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_from_approval_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(scope = 'global' AND topic_id IS NULL AND chat_id IS NULL)"
            " OR (scope = 'topic' AND topic_id IS NOT NULL AND chat_id IS NULL)"
            " OR (scope = 'chat' AND chat_id IS NOT NULL AND topic_id IS NULL)",
            name="ck_approval_rule_scope",
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approval_rules_topic_id", "approval_rules", ["topic_id"])
    op.create_index("ix_approval_rules_chat_id", "approval_rules", ["chat_id"])

    # Carry over "Always allow here" answers: one allow rule per conversation and tool.
    op.execute(
        sa.text(
            "INSERT INTO approval_rules (effect, server, tool, arg_match, scope, topic_id, chat_id,"
            " enabled, hit_count, created_from_approval_id)"
            " SELECT 'allow', server, raw_tool, '[]',"
            " CASE WHEN topic_id IS NOT NULL THEN 'topic' ELSE 'chat' END,"
            " topic_id, chat_id, 1, 0, MIN(id)"
            " FROM tool_approvals"
            " WHERE kind = 'tool' AND status = 'approved' AND scope = 'conversation'"
            " AND server <> '' AND raw_tool <> ''"
            " GROUP BY topic_id, chat_id, server, raw_tool"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_approval_rules_chat_id", table_name="approval_rules")
    op.drop_index("ix_approval_rules_topic_id", table_name="approval_rules")
    op.drop_table("approval_rules")
