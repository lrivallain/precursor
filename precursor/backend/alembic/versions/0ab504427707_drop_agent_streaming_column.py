"""drop agent streaming column

Revision ID: 0ab504427707
Revises: c04d5ffe8e4c
Create Date: 2026-06-23 21:47:31.965770

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0ab504427707"
down_revision: str | None = "c04d5ffe8e4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_sessions") as batch_op:
        batch_op.drop_column("streaming")


def downgrade() -> None:
    with op.batch_alter_table("agent_sessions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "streaming",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
