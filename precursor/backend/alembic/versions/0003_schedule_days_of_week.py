"""add days_of_week to topic_schedule

Revision ID: 0003_schedule_days_of_week
Revises: 0002_scheduled_topics
Create Date: 2026-06-14

Adds the ``days_of_week`` weekday mask to ``topic_schedule`` so scheduled
topics can be restricted to specific days (e.g. weekdays only). Guarded to be a
no-op when the column already exists.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0003_schedule_days_of_week"
down_revision = "0002_scheduled_topics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "topic_schedule" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("topic_schedule")}
    if "days_of_week" not in cols:
        op.add_column(
            "topic_schedule",
            sa.Column(
                "days_of_week",
                sa.Integer(),
                nullable=False,
                server_default="127",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "topic_schedule" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("topic_schedule")}
    if "days_of_week" in cols:
        op.drop_column("topic_schedule", "days_of_week")
