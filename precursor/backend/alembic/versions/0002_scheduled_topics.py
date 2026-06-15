"""add scheduled topics (topics.kind + topic_schedule)

Revision ID: 0002_scheduled_topics
Revises: 0001_workspaces
Create Date: 2026-06-14

Adds the ``kind`` discriminator to ``topics`` and creates the
``topic_schedule`` table that backs recurring "scheduled" topics. Guarded so it
is a no-op on databases where ``create_all`` already produced the new shape.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0002_scheduled_topics"
down_revision = "0001_workspaces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "topics" in tables:
        cols = {c["name"] for c in inspector.get_columns("topics")}
        if "kind" not in cols:
            op.add_column(
                "topics",
                sa.Column(
                    "kind",
                    sa.String(length=32),
                    nullable=False,
                    server_default="standard",
                ),
            )

    if "topic_schedule" not in tables:
        op.create_table(
            "topic_schedule",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "topic_id",
                sa.Integer(),
                sa.ForeignKey("topics.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column(
                "enabled",
                sa.Boolean(),
                nullable=False,
                server_default="1",
            ),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("interval_seconds", sa.Integer(), nullable=False),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="idle",
            ),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index("ix_topic_schedule_topic_id", "topic_schedule", ["topic_id"])
        op.create_index("ix_topic_schedule_next_run_at", "topic_schedule", ["next_run_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "topic_schedule" in tables:
        op.drop_index("ix_topic_schedule_next_run_at", table_name="topic_schedule")
        op.drop_index("ix_topic_schedule_topic_id", table_name="topic_schedule")
        op.drop_table("topic_schedule")

    if "topics" in tables:
        cols = {c["name"] for c in inspector.get_columns("topics")}
        if "kind" in cols:
            op.drop_column("topics", "kind")
