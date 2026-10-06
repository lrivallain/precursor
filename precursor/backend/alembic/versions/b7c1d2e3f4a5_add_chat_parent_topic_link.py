"""side chats: link a chat to its parent topic and source reply

Revision ID: b7c1d2e3f4a5
Revises: 450b434e27c0
Create Date: 2026-10-06 16:10:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c1d2e3f4a5"
down_revision: str | None = "450b434e27c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("parent_topic_id", sa.Integer(), nullable=True))
    op.add_column("chats", sa.Column("parent_message_id", sa.Integer(), nullable=True))
    op.add_column("chats", sa.Column("seed_content", sa.Text(), nullable=True))
    op.create_index(op.f("ix_chats_parent_topic_id"), "chats", ["parent_topic_id"], unique=False)
    op.create_index(
        op.f("ix_chats_parent_message_id"), "chats", ["parent_message_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_chats_parent_message_id"), table_name="chats")
    op.drop_index(op.f("ix_chats_parent_topic_id"), table_name="chats")
    op.drop_column("chats", "seed_content")
    op.drop_column("chats", "parent_message_id")
    op.drop_column("chats", "parent_topic_id")
