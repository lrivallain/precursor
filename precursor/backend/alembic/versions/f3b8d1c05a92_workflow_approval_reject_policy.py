"""workflow approval reject policy

Adds ``workflow_steps.on_reject`` — what a human rejection at an ``approval``
checkpoint does next: ``rework`` (default; send the work back to an earlier step),
``stop`` (end the run — the answer is "don't do this at all", the point of a
checkpoint in front of an irreversible action), or ``skip`` (abandon the rejected
work and carry on).

Revision ID: f3b8d1c05a92
Revises: e7c2f4a9b8d1
Create Date: 2026-08-21 15:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3b8d1c05a92"
down_revision = "e7c2f4a9b8d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_steps",
        sa.Column("on_reject", sa.String(length=16), nullable=False, server_default="rework"),
    )


def downgrade() -> None:
    op.drop_column("workflow_steps", "on_reject")
