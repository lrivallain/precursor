"""workflow run-step replay flag

Adds ``workflow_run_steps.replay`` — true when an attempt was a **manual replay**
of one step rather than a turn the pipeline itself drove. A replay re-runs a
single step out of band on the exact input a previous attempt saw, and nothing
advances when it ends, so the coordinator's "which turn is the run waiting on?"
lookups exclude these rows and the trace badges them separately.

Existing rows are pipeline attempts, hence the ``0`` default.

Revision ID: 4fd7f3a6c9e4
Revises: a3d7c1e9f204
Create Date: 2026-08-14 08:12:09.214141
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "4fd7f3a6c9e4"
down_revision = "a3d7c1e9f204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_run_steps",
        sa.Column("replay", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("workflow_run_steps", "replay")
