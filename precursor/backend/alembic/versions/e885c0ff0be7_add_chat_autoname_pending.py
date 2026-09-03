"""add chat autoname_pending

Revision ID: e885c0ff0be7
Revises: c5a0f81b7d34
Create Date: 2026-09-03 14:55:23.210359

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e885c0ff0be7"
down_revision: str | None = "c5a0f81b7d34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chats", sa.Column("autoname_pending", sa.Boolean(), server_default="0", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("chats", "autoname_pending")
