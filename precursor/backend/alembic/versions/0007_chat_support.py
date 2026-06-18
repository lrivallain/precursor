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

    # Modify the messages table to also host chat turns: add a nullable chat_id,
    # relax topic_id to nullable, and enforce that exactly one container is set.
    # batch_alter_table makes this work on SQLite (which can't ALTER a column's
    # nullability or add a CHECK in place) as well as on Postgres.
    bind = op.get_bind()
    inspector = inspect(bind)
    cols = {c["name"]: c for c in inspector.get_columns("messages")}
    constraints = {c["name"] for c in inspector.get_check_constraints("messages")}

    needs_chat_id = "chat_id" not in cols
    needs_nullable = cols.get("topic_id", {}).get("nullable") is False
    needs_constraint = "ck_message_container" not in constraints
    if not (needs_chat_id or needs_nullable or needs_constraint):
        return

    with op.batch_alter_table("messages", recreate="always") as batch:
        if needs_nullable:
            batch.alter_column("topic_id", existing_type=sa.Integer(), nullable=True)
        if needs_chat_id:
            batch.add_column(
                sa.Column(
                    "chat_id",
                    sa.Integer,
                    sa.ForeignKey("chats.id", ondelete="CASCADE"),
                    nullable=True,
                )
            )
            batch.create_index(op.f("ix_messages_chat_id"), ["chat_id"])
        if needs_constraint:
            batch.create_check_constraint(
                "ck_message_container",
                "(topic_id IS NOT NULL AND chat_id IS NULL) "
                "OR (topic_id IS NULL AND chat_id IS NOT NULL)",
            )


def downgrade() -> None:
    # This is a complex migration; downgrade is not fully supported.
    # In a production scenario, you'd carefully handle this.
    pass
