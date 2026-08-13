"""add workflow approval policy

Revision ID: d686a8d80c27
Revises: 167f9c4e5752
Create Date: 2026-08-13 11:57:50.374988

A workflow-wide tool-approval policy applied to every step's agent while the
pipeline runs. Nullable: null keeps today's behaviour of leaving each agent's
own setting alone.

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d686a8d80c27"
down_revision: str | None = "167f9c4e5752"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workflows", sa.Column("approval_policy", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("workflows", "approval_policy")
