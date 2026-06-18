"""add reminders table

Revision ID: 0009_reminders
Revises: 0008_chat_attachments
Create Date: 2026-06-18

Adds the ``reminders`` table for one-shot date/time reminders attached to either
a topic or a chat (exactly one container, mirroring ``messages``). At most one
reminder per container (unique topic_id / chat_id).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0009_reminders"
down_revision = "0008_chat_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reminders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "topic_id",
            sa.Integer,
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            nullable=True,
            unique=True,
        ),
        sa.Column(
            "chat_id",
            sa.Integer,
            sa.ForeignKey("chats.id", ondelete="CASCADE"),
            nullable=True,
            unique=True,
        ),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="scheduled"),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(topic_id IS NOT NULL AND chat_id IS NULL) "
            "OR (topic_id IS NULL AND chat_id IS NOT NULL)",
            name="ck_reminder_container",
        ),
    )
    op.create_index(op.f("ix_reminders_topic_id"), "reminders", ["topic_id"])
    op.create_index(op.f("ix_reminders_chat_id"), "reminders", ["chat_id"])
    op.create_index(op.f("ix_reminders_remind_at"), "reminders", ["remind_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_reminders_remind_at"), table_name="reminders")
    op.drop_index(op.f("ix_reminders_chat_id"), table_name="reminders")
    op.drop_index(op.f("ix_reminders_topic_id"), table_name="reminders")
    op.drop_table("reminders")
