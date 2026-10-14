"""conversation llm selections

Revision ID: eb850c375f74
Revises: 5ec9892c32b6
Create Date: 2026-10-14 10:43:09.998373

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "eb850c375f74"
down_revision: str | None = "5ec9892c32b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("llm_selection", sa.Text(), nullable=True))
    op.add_column("topics", sa.Column("llm_selection", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("topics", "llm_selection")
    op.drop_column("chats", "llm_selection")
