"""version history for a step plan's final output

Revision ID: d5e8f3a1b274
Revises: c4a7d2e91f30
Create Date: 2026-10-29 10:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e8f3a1b274"
down_revision: str | None = "c4a7d2e91f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "step_plans",
        sa.Column("final_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_table(
        "step_plan_final_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=8), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_message_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["step_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_step_plan_final_versions_plan_id"),
        "step_plan_final_versions",
        ["plan_id"],
        unique=False,
    )
    # A final output written before versions existed becomes version 1.
    op.execute(
        "INSERT INTO step_plan_final_versions (plan_id, version, content, author) "
        "SELECT id, 1, final_output, 'user' FROM step_plans "
        "WHERE final_output IS NOT NULL AND TRIM(final_output) <> ''"
    )
    op.execute(
        "UPDATE step_plans SET final_version = 1 "
        "WHERE final_output IS NOT NULL AND TRIM(final_output) <> ''"
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_step_plan_final_versions_plan_id"), table_name="step_plan_final_versions"
    )
    op.drop_table("step_plan_final_versions")
    op.drop_column("step_plans", "final_version")
