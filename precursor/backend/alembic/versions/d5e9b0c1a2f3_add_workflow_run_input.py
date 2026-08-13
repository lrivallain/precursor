"""workflow per-run input

Adds ``workflow_runs.input`` — an optional brief supplied by whoever triggered the
run (the file to analyse, the topic to research, the payload a webhook posted).
The workflow *definition* stays generic and reusable; the input is the run's
subject, injected into every step's kickoff preamble so the whole pipeline shares
the same intent. Null means "no brief" and the run proceeds on its steps' own
objectives alone.

Revision ID: d5e9b0c1a2f3
Revises: c3f7a1b2d4e8
Create Date: 2026-08-21 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d5e9b0c1a2f3"
down_revision = "c3f7a1b2d4e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_runs", sa.Column("input", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("workflow_runs", "input")
