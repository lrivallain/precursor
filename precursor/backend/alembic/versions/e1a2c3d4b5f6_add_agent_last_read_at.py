"""add agent_sessions.last_read_at

Tracks when the user last opened an agent session so the Agents list can show an
unread badge for background/scheduled replies, mirroring topics.last_read_at and
chats.last_read_at.

Revision ID: e1a2c3d4b5f6
Revises: c7a1e2b3d4f5
Create Date: 2026-07-01 14:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1a2c3d4b5f6"
down_revision: str | None = "c7a1e2b3d4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "last_read_at")
