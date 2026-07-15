"""add meeting_sessions.topic_summary

Caches the AI summary of a live session's attached topic so the Context tab
serves it from storage instead of re-summarizing (and re-spending tokens) on
every display. Cleared when the attached topic changes.

Revision ID: d1b38ab39d00
Revises: b548d127e6b3
Create Date: 2026-07-15 15:55:32.553603

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1b38ab39d00"
down_revision: str | None = "b548d127e6b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("meeting_sessions", sa.Column("topic_summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("meeting_sessions", "topic_summary")
