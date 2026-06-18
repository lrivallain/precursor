"""add usage_records ledger table

Revision ID: 0009_usage_records
Revises: 0008_chat_attachments
Create Date: 2026-06-18

Adds the ``usage_records`` table — a ledger of every metered LLM round-trip
(chat turns, tool rounds, and utility commands like ``/notes`` or
``/gh-create``). This ledger is the single source of truth for the global
usage statistics so utility calls that persist no conversation message are
still counted.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0009_usage_records"
down_revision = "0008_chat_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_records",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.String(64), nullable=False, server_default="chat"),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column(
            "topic_id",
            sa.Integer,
            sa.ForeignKey("topics.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "chat_id",
            sa.Integer,
            sa.ForeignKey("chats.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("prompt_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_usage_records_topic_id"), "usage_records", ["topic_id"])
    op.create_index(op.f("ix_usage_records_chat_id"), "usage_records", ["chat_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_usage_records_chat_id"), table_name="usage_records")
    op.drop_index(op.f("ix_usage_records_topic_id"), table_name="usage_records")
    op.drop_table("usage_records")
