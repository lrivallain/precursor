"""move attachment blobs from db to disk (content-addressed)

Revision ID: f1a2b3c4d5e6
Revises: e1a2c3d4b5f6
Create Date: 2026-07-10 16:20:00.000000

Attachment payloads move out of the ``data`` BLOB column and onto disk as
content-addressed files under ``settings.blobs_dir`` (see
``services/blob_store.py``). The row keeps a ``sha256`` pointer instead. Both
``attachments`` and ``note_draft_attachments`` are migrated; existing blobs are
written to disk during the upgrade so nothing is lost.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e1a2c3d4b5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("attachments", "note_draft_attachments")


def upgrade() -> None:
    bind = op.get_bind()

    for table in _TABLES:
        op.add_column(table, sa.Column("sha256", sa.String(length=64), nullable=True))

    # Spill existing BLOBs to disk and record their SHA-256 pointer.
    from precursor.backend.services.blob_store import write_blob

    for table in _TABLES:
        rows = bind.execute(sa.text(f"SELECT id, data FROM {table}")).fetchall()
        for row_id, data in rows:
            if data is None:
                continue
            sha = write_blob(bytes(data))
            bind.execute(
                sa.text(f"UPDATE {table} SET sha256 = :sha WHERE id = :id"),
                {"sha": sha, "id": row_id},
            )

    # Rebuild each table without ``data`` and with a NOT NULL, indexed sha256.
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("sha256", existing_type=sa.String(length=64), nullable=False)
            batch_op.create_index(op.f(f"ix_{table}_sha256"), ["sha256"], unique=False)
            batch_op.drop_column("data")


def downgrade() -> None:
    bind = op.get_bind()

    for table in _TABLES:
        op.add_column(table, sa.Column("data", sa.LargeBinary(), nullable=True))

    # Read the content back from disk into the restored BLOB column.
    from precursor.backend.services.blob_store import read_blob

    for table in _TABLES:
        rows = bind.execute(sa.text(f"SELECT id, sha256 FROM {table}")).fetchall()
        for row_id, sha in rows:
            if not sha:
                continue
            try:
                data = read_blob(sha)
            except FileNotFoundError:
                continue
            bind.execute(
                sa.text(f"UPDATE {table} SET data = :data WHERE id = :id"),
                {"data": data, "id": row_id},
            )

    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("data", existing_type=sa.LargeBinary(), nullable=False)
            batch_op.drop_index(op.f(f"ix_{table}_sha256"))
            batch_op.drop_column("sha256")
