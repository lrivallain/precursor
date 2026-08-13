"""workflow gate loop-back

Adds conditional loop-back to workflows: a ``gate`` step votes PASS/FAIL and, on
FAIL, re-drives an earlier step until it passes or a per-run loop cap is reached.

* ``workflows.max_loops`` — safety cap on gate loop-backs (default 3).
* ``workflow_steps.kind`` — ``task`` (default) or ``gate``.
* ``workflow_steps.on_fail_position`` — gate FAIL target (null = previous step).
* ``workflow_steps.attempt_count`` — per-run re-drive counter.

Revision ID: a7c93e21f4d8
Revises: b1dfc71e2cfd
Create Date: 2026-08-13 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7c93e21f4d8"
down_revision = "b1dfc71e2cfd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("max_loops", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "workflow_steps",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="task"),
    )
    op.add_column(
        "workflow_steps",
        sa.Column("on_fail_position", sa.Integer(), nullable=True),
    )
    op.add_column(
        "workflow_steps",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("workflow_steps", "attempt_count")
    op.drop_column("workflow_steps", "on_fail_position")
    op.drop_column("workflow_steps", "kind")
    op.drop_column("workflows", "max_loops")
