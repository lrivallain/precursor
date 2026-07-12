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
    # NOTE: plain ADD COLUMN (no batch recreate). SQLite can't ADD a FK
    # constraint via ALTER, and batch-recreating meeting_sessions hangs once the
    # table has child rows (segments/insights/attachments) under
    # foreign_keys=ON. The ORM still models the chats.id relationship; a dangling
    # chat_id (deleted chat) is tolerated — ensure_chat re-fetches and re-spawns.
    op.add_column("meeting_sessions", sa.Column("chat_id", sa.Integer(), nullable=True))
    op.create_index(
        op.f("ix_meeting_sessions_chat_id"), "meeting_sessions", ["chat_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_meeting_sessions_chat_id"), table_name="meeting_sessions")
    op.drop_column("meeting_sessions", "chat_id")
