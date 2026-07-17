"""add role_id to meeting_sessions and agent_sessions

Extends Assistant Roles beyond topics/chats/workspaces to Live meeting sessions
and Agents. A null ``role_id`` resolves to the default role (no persona).

NOTE: plain ADD COLUMN (no batch recreate) — SQLite can't ADD a FK constraint
via ALTER, and batch-recreating these tables hangs once they have child rows
under foreign_keys=ON (mirrors the chat_id migration). The ORM still models the
roles.id relationship; a since-deleted role is tolerated (``resolve_role_prompt``
falls back to default) and the app layer clears the refs on role delete.

Revision ID: a1b2c3d4e5f6
Revises: 4fbf2183a9da
Create Date: 2026-07-17 16:40:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "4fbf2183a9da"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("meeting_sessions", "agent_sessions"):
        op.add_column(table, sa.Column("role_id", sa.Integer(), nullable=True))
        op.create_index(op.f(f"ix_{table}_role_id"), table, ["role_id"], unique=False)


def downgrade() -> None:
    for table in ("meeting_sessions", "agent_sessions"):
        op.drop_index(op.f(f"ix_{table}_role_id"), table_name=table)
        op.drop_column(table, "role_id")
