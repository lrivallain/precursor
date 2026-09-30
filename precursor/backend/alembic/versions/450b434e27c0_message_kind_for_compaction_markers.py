"""message kind for compaction markers

Marks special transcript rows; "compaction" is a context-compaction summary.

Revision ID: 450b434e27c0
Revises: 9b4e2f7a1c3d
Create Date: 2026-09-30 12:35:28.356765

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "450b434e27c0"
down_revision: str | None = "9b4e2f7a1c3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("kind", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "kind")
