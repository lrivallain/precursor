"""message reply edits

An assistant reply the user edited keeps the model's answer in
``original_content`` (so the edit can be undone) and when it was edited.

Revision ID: 5ec9892c32b6
Revises: c8d2e3f4a5b6
Create Date: 2026-10-08 09:38:00.457656

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5ec9892c32b6"
down_revision: str | None = "c8d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("original_content", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "edited_at")
    op.drop_column("messages", "original_content")
