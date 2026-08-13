"""add default_role_id to collections

Lets each Collection carry a default Assistant Role that newer topics created in
it inherit when the caller doesn't pick one. A null ``default_role_id`` means
"use the built-in default role".

NOTE: plain ADD COLUMN (no batch recreate) — SQLite can't ADD a FK constraint
via ALTER, and batch-recreating a table hangs once it has child rows under
foreign_keys=ON (mirrors the role_id/chat_id migrations). The ORM still models
the roles.id relationship; a since-deleted role is tolerated (the collection
default simply falls back to the built-in default) and the app layer clears the
ref on role delete.

Revision ID: b2c3d4e5f6a7
Revises: 8ae5cacca272
Create Date: 2026-07-18 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "8ae5cacca272"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("collections", sa.Column("default_role_id", sa.Integer(), nullable=True))
    op.create_index(
        op.f("ix_collections_default_role_id"),
        "collections",
        ["default_role_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_collections_default_role_id"), table_name="collections")
    op.drop_column("collections", "default_role_id")
