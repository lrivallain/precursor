"""message provider

Revision ID: a7c19e3b5d42
Revises: eb850c375f74
Create Date: 2026-10-14 14:10:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c19e3b5d42"
down_revision: str | None = "eb850c375f74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("provider", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "provider")
