"""add chats table and chat support to messages

Revision ID: 0007_chat_support
Revises: 0006_schedule_clear_context
Create Date: 2026-06-17

Adds the ``chats`` table for flat conversation sessions without tree hierarchy
or GitHub issue associations. Updates the ``messages`` table to support both
topic and chat messages via topic_id/chat_id foreign keys (exactly one must
be set).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0007_chat_support"
down_revision = "0006_schedule_clear_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the chats table.
    op.create_table(
        "chats",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pinned", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_chats_slug"), "chats", ["slug"])

    # Modify messages table to support chats.
    bind = op.get_bind()
    inspector = inspect(bind)

    # Check if topic_id is already nullable (skip if already applied).
    cols = {c["name"]: c for c in inspector.get_columns("messages")}
    if cols.get("topic_id", {}).get("nullable") is False:
        # Make topic_id nullable and add chat_id.
        op.alter_column("messages", "topic_id", existing_type=sa.Integer, nullable=True)

    # Add chat_id if it doesn't exist.
    if "chat_id" not in cols:
        op.add_column(
            "messages",
            sa.Column(
                "chat_id",
                sa.Integer,
                sa.ForeignKey("chats.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
        op.create_index(op.f("ix_messages_chat_id"), "messages", ["chat_id"])

    # Add the check constraint if it doesn't exist.
    # Note: This is platform-specific; some databases may not enforce it.
    constraints = {c["name"] for c in inspector.get_check_constraints("messages")}
    if "ck_message_container" not in constraints:
        op.create_check_constraint(
            "ck_message_container",
            "messages",
            "(topic_id IS NOT NULL AND chat_id IS NULL) OR (topic_id IS NULL AND chat_id IS NOT NULL)",
        )


def downgrade() -> None:
    # This is a complex migration; downgrade is not fully supported.
    # In a production scenario, you'd carefully handle this.
    pass
