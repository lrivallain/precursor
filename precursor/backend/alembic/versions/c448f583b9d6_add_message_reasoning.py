"""add message reasoning

Revision ID: c448f583b9d6
Revises: 5d2e8f41a7b3
Create Date: 2026-09-29 10:55:18.439904

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c448f583b9d6"
down_revision: str | None = "5d2e8f41a7b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("reasoning", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "reasoning")
