"""message and chat permalink identities

Revision ID: 5fa65fad981a
Revises: d5e8f3a1b274
Create Date: 2026-11-04 11:52:06.216189
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "5fa65fad981a"
down_revision: str | None = "d5e8f3a1b274"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    for table in ("chats", "messages"):
        op.add_column(table, sa.Column("public_id", sa.String(64), nullable=True))
        # Backfill in bounded batches: a large transcript must not be held in RAM.
        last_id = -1
        while rows := bind.execute(
            sa.text(f"SELECT id FROM {table} WHERE id > :last ORDER BY id LIMIT 1000"),
            {"last": last_id},
        ).fetchall():
            bind.execute(
                sa.text(f"UPDATE {table} SET public_id = :pid WHERE id = :id"),
                [{"pid": str(uuid4()), "id": row[0]} for row in rows],
            )
            last_id = rows[-1][0]
        with op.batch_alter_table(table) as batch:
            batch.alter_column("public_id", existing_type=sa.String(64), nullable=False)
            batch.create_index(f"ix_{table}_public_id", ["public_id"], unique=True)


def downgrade() -> None:
    for table in ("messages", "chats"):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_public_id")
            batch.drop_column("public_id")
