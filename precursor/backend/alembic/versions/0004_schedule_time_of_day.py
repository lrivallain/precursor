"""add run_at_minute + timezone to topic_schedule

Revision ID: 0004_schedule_time_of_day
Revises: 0003_schedule_days_of_week
Create Date: 2026-06-14

Adds daily-at-time recurrence support to ``topic_schedule``: ``run_at_minute``
(minutes since local midnight; null = interval mode) and ``timezone`` (IANA
name used to interpret the time). Guarded to be a no-op when the columns
already exist.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0004_schedule_time_of_day"
down_revision = "0003_schedule_days_of_week"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "topic_schedule" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("topic_schedule")}
    if "run_at_minute" not in cols:
        op.add_column(
            "topic_schedule",
            sa.Column("run_at_minute", sa.Integer(), nullable=True),
        )
    if "timezone" not in cols:
        op.add_column(
            "topic_schedule",
            sa.Column(
                "timezone",
                sa.String(length=64),
                nullable=False,
                server_default="UTC",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "topic_schedule" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("topic_schedule")}
    if "timezone" in cols:
        op.drop_column("topic_schedule", "timezone")
    if "run_at_minute" in cols:
        op.drop_column("topic_schedule", "run_at_minute")
