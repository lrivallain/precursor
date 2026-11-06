"""archive step plans

Revision ID: e83eedc78f50
Revises: 0e4480c7862e
Create Date: 2026-11-05 18:19:16.265029

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e83eedc78f50"
down_revision: str | None = "0e4480c7862e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("step_plans", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("step_plans", "archived_at")
