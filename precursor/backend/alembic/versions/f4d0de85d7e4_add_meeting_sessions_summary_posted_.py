"""add meeting_sessions summary + posted fields

Persists the generated meeting recap so a reopened session shows it without
regenerating, and records when/where the recap was posted to a topic.

Revision ID: f4d0de85d7e4
Revises: d1b38ab39d00
Create Date: 2026-07-15 16:52:10.223370

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4d0de85d7e4"
down_revision: str | None = "d1b38ab39d00"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("meeting_sessions", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "meeting_sessions",
        sa.Column("summary_posted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "meeting_sessions", sa.Column("summary_posted_topic_id", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("meeting_sessions", "summary_posted_topic_id")
    op.drop_column("meeting_sessions", "summary_posted_at")
    op.drop_column("meeting_sessions", "summary")
