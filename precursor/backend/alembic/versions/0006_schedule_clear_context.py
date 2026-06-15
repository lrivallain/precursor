"""add clear_context to topic_schedule

Revision ID: 0006_schedule_clear_context
Revises: 0005_local_workspaces
Create Date: 2026-06-14

Adds the ``clear_context`` flag to ``topic_schedule`` so a scheduled topic can
wipe its prior messages before each run. Guarded to be a no-op when already
applied.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0006_schedule_clear_context"
down_revision = "0005_local_workspaces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "topic_schedule" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("topic_schedule")}
    if "clear_context" not in cols:
        op.add_column(
            "topic_schedule",
            sa.Column(
                "clear_context",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "topic_schedule" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("topic_schedule")}
    if "clear_context" in cols:
        op.drop_column("topic_schedule", "clear_context")
