"""scheduled topics become standard topics with a schedule

Drops the special scheduled-topic model: a topic is now "scheduled" simply when
it has a TopicSchedule row. Existing ``kind='scheduled'`` topics become normal
``standard`` topics (keeping their schedule), their synthetic ``schedule_root``
parent folder's children are lifted to the top level, and the now-empty
``schedule_root`` folder(s) are removed.

Revision ID: c7a1e2b3d4f5
Revises: 949c707eb929
Create Date: 2026-06-30 12:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c7a1e2b3d4f5"
down_revision: str | None = "949c707eb929"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Lift any topic parented under a schedule_root folder to the top level.
    op.execute(
        "UPDATE topics SET parent_id = NULL "
        "WHERE parent_id IN (SELECT id FROM topics WHERE kind = 'schedule_root')"
    )
    # 2. Scheduled topics become ordinary topics (their schedule row is unchanged).
    op.execute("UPDATE topics SET kind = 'standard' WHERE kind = 'scheduled'")
    # 3. Remove the now-empty synthetic Scheduled folder(s).
    op.execute("DELETE FROM topics WHERE kind = 'schedule_root'")


def downgrade() -> None:
    # One-way data migration: the schedule_root folder and the scheduled/
    # schedule_root kinds aren't reconstructed (schedules are preserved, so the
    # feature keeps working without them).
    pass
