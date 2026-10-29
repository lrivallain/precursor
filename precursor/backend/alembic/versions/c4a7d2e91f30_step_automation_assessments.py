"""step automation mode, assessments and approval review flag

Revision ID: c4a7d2e91f30
Revises: b87e1921ac3c
Create Date: 2026-10-28 21:10:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4a7d2e91f30"
down_revision: str | None = "b87e1921ac3c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "step_plans",
        sa.Column("automation_mode", sa.String(length=12), server_default="manual", nullable=False),
    )
    op.add_column(
        "step_plans",
        sa.Column("auto_state", sa.String(length=12), server_default="idle", nullable=False),
    )
    op.add_column("step_plans", sa.Column("assessment_json", sa.Text(), nullable=True))
    op.add_column("step_plans", sa.Column("assessed_sig", sa.String(length=40), nullable=True))
    op.add_column("steps", sa.Column("assessment_json", sa.Text(), nullable=True))
    op.add_column("steps", sa.Column("assessed_sig", sa.String(length=40), nullable=True))
    op.add_column(
        "steps",
        sa.Column("needs_review", sa.Boolean(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("steps", "needs_review")
    op.drop_column("steps", "assessed_sig")
    op.drop_column("steps", "assessment_json")
    op.drop_column("step_plans", "assessed_sig")
    op.drop_column("step_plans", "assessment_json")
    op.drop_column("step_plans", "auto_state")
    op.drop_column("step_plans", "automation_mode")
