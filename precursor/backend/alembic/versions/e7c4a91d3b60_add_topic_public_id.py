"""add topics.public_id and re-home collection-less topics

Two related bits of topic addressing:

* ``topics.public_id`` — an immutable UUID permalink. The readable URL carries
  the collection slug and the ancestor chain, so it moves whenever a topic is
  renamed, re-parented or moved between collections; ``/t/<public_id>`` never
  does. Backfilled with a fresh UUID per existing row.
* ``topics.collection_id`` backfill — chat promotion and the MCP
  ``create_schedule`` tool used to insert topics without a collection, and a
  null membership matches no collection filter, so those topics were invisible
  in the sidebar. Re-home them onto the protected default collection.

Revision ID: e7c4a91d3b60
Revises: b6c8e14a92df
Create Date: 2026-08-24 10:24:00.000000

"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7c4a91d3b60"
down_revision: str | None = "b6c8e14a92df"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("topics", sa.Column("public_id", sa.String(length=64), nullable=True))
    rows = bind.execute(sa.text("SELECT id FROM topics WHERE public_id IS NULL")).fetchall()
    for (topic_id,) in rows:
        bind.execute(
            sa.text("UPDATE topics SET public_id = :pid WHERE id = :tid"),
            {"pid": str(uuid.uuid4()), "tid": topic_id},
        )
    with op.batch_alter_table("topics") as batch_op:
        batch_op.alter_column("public_id", existing_type=sa.String(length=64), nullable=False)
    op.create_index(op.f("ix_topics_public_id"), "topics", ["public_id"], unique=True)

    # A null collection matches no sidebar filter, stranding the topic. Fall
    # back to the protected default (matched by name when an older database
    # predates `is_default`).
    op.execute(
        sa.text(
            "UPDATE topics SET collection_id = ("
            "  SELECT id FROM collections"
            "  ORDER BY is_default DESC, CASE WHEN lower(name) = 'general' THEN 0 ELSE 1 END, id"
            "  LIMIT 1"
            ") WHERE collection_id IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_topics_public_id"), table_name="topics")
    op.drop_column("topics", "public_id")
