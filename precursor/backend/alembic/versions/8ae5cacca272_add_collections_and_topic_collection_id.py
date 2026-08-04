"""add collections and topic collection_id

Collections group topics into switchable sets that filter the sidebar tree, and
carry an optional GitHub repo override sitting between the topic's own override
and the global setting.

Seeds the protected default collection ("General") and backfills every existing
topic into it so no topic is ever collection-less.

NOTE: plain ADD COLUMN (no batch recreate) for ``topics.collection_id`` —
SQLite can't ADD a FK constraint via ALTER, and batch-recreating ``topics``
hangs once it has child rows under foreign_keys=ON (mirrors the role_id
migration). The ORM still models the collections.id relationship; the app layer
re-homes topics when a collection is deleted.

Revision ID: 8ae5cacca272
Revises: a1b2c3d4e5f6
Create Date: 2026-08-04 10:52:57.676648

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8ae5cacca272"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "collections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("github_repo", sa.String(length=255), nullable=True),
        sa.Column("accent", sa.String(length=32), server_default="sky", nullable=False),
        sa.Column("icon", sa.String(length=64), nullable=True),
        sa.Column("is_default", sa.Boolean(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_collections_name"), "collections", ["name"], unique=True)
    op.create_index(op.f("ix_collections_slug"), "collections", ["slug"], unique=True)

    op.execute(
        sa.text(
            "INSERT INTO collections (name, slug, accent, is_default, created_at, updated_at) "
            "VALUES ('General', 'general', 'sky', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )

    op.add_column("topics", sa.Column("collection_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_topics_collection_id"), "topics", ["collection_id"], unique=False)
    op.execute(
        sa.text(
            "UPDATE topics SET collection_id = "
            "(SELECT id FROM collections WHERE is_default = 1 LIMIT 1)"
        )
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_topics_collection_id"), table_name="topics")
    op.drop_column("topics", "collection_id")
    op.drop_index(op.f("ix_collections_slug"), table_name="collections")
    op.drop_index(op.f("ix_collections_name"), table_name="collections")
    op.drop_table("collections")
