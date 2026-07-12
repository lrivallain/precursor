"""add chat_id to meeting_sessions

Revision ID: 4083e9c16be8
Revises: 22ce95ab9f0a
Create Date: 2026-07-12 13:07:13.549899

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4083e9c16be8"
down_revision: str | None = "22ce95ab9f0a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("meeting_sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("chat_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_meeting_sessions_chat_id"), ["chat_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_meeting_sessions_chat_id_chats",
            "chats",
            ["chat_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("meeting_sessions", schema=None) as batch_op:
        batch_op.drop_constraint("fk_meeting_sessions_chat_id_chats", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_meeting_sessions_chat_id"))
        batch_op.drop_column("chat_id")
