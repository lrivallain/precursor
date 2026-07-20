"""cockpit autostart

Revision ID: 723625fe5a4f
Revises: 20e5dfd1fffd
Create Date: 2026-07-20 12:14:53.184474

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "723625fe5a4f"
down_revision: str | None = "20e5dfd1fffd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive, idempotent ADD COLUMN (SQLite DDL isn't transactional, so a
    # previously failed run can leave the column already added).
    bind = op.get_bind()
    existing = {col["name"] for col in sa.inspect(bind).get_columns("cockpits")}
    if "autostart" not in existing:
        op.add_column(
            "cockpits", sa.Column("autostart", sa.Boolean(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = {col["name"] for col in sa.inspect(bind).get_columns("cockpits")}
    if "autostart" in existing:
        op.drop_column("cockpits", "autostart")
