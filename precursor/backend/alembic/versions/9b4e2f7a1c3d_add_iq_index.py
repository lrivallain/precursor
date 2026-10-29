"""add Precursor IQ retrieval index

Revision ID: 9b4e2f7a1c3d
Revises: c448f583b9d6
Create Date: 2026-09-30 10:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b4e2f7a1c3d"
down_revision: str | None = "c448f583b9d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The full-text side isn't expressible as mapped columns (an FTS5 virtual table
# on SQLite, a generated tsvector on Postgres), so it's plain DDL here and
# ``alembic/env.py`` keeps autogenerate from proposing to drop it.
_SQLITE_FTS = [
    "CREATE VIRTUAL TABLE iq_chunks_fts USING fts5("
    "heading, text, content='iq_chunks', content_rowid='id', "
    "tokenize='unicode61 remove_diacritics 2')",
    "CREATE TRIGGER iq_chunks_ai AFTER INSERT ON iq_chunks BEGIN "
    "INSERT INTO iq_chunks_fts(rowid, heading, text) VALUES (new.id, new.heading, new.text); "
    "END",
    "CREATE TRIGGER iq_chunks_ad AFTER DELETE ON iq_chunks BEGIN "
    "INSERT INTO iq_chunks_fts(iq_chunks_fts, rowid, heading, text) "
    "VALUES ('delete', old.id, old.heading, old.text); "
    "END",
    # Only heading/text changes touch the index; an embedding write doesn't.
    "CREATE TRIGGER iq_chunks_au AFTER UPDATE OF heading, text ON iq_chunks BEGIN "
    "INSERT INTO iq_chunks_fts(iq_chunks_fts, rowid, heading, text) "
    "VALUES ('delete', old.id, old.heading, old.text); "
    "INSERT INTO iq_chunks_fts(rowid, heading, text) VALUES (new.id, new.heading, new.text); "
    "END",
]

_POSTGRES_FTS = [
    "ALTER TABLE iq_chunks ADD COLUMN text_tsv tsvector GENERATED ALWAYS AS ("
    "setweight(to_tsvector('simple', coalesce(heading, '')), 'A') || "
    "setweight(to_tsvector('simple', coalesce(text, '')), 'B')) STORED",
    "CREATE INDEX ix_iq_chunks_tsv ON iq_chunks USING gin (text_tsv)",
]


def _sqlite_has_fts5() -> bool:
    bind = op.get_bind()
    try:
        return bool(
            bind.exec_driver_sql("SELECT sqlite_compileoption_used('ENABLE_FTS5')").scalar()
        )
    except Exception:
        return False


def upgrade() -> None:
    op.create_table(
        "iq_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("chunk_no", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(length=32), nullable=False),
        sa.Column("container_kind", sa.String(length=16), nullable=False),
        sa.Column("container_id", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=True),
        sa.Column("heading", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.Column("embedding_model", sa.String(length=160), nullable=True),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_kind", "source_id", "chunk_no", name="uq_iq_chunks_source"),
    )
    op.create_index("ix_iq_chunks_container", "iq_chunks", ["container_kind", "container_id"])
    op.create_index("ix_iq_chunks_embedding_model", "iq_chunks", ["embedding_model"])
    op.create_index(op.f("ix_iq_chunks_section"), "iq_chunks", ["section"])
    op.create_table(
        "iq_dirty",
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source_kind", "source_id"),
    )

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        # A build without FTS5 still works: retrieval falls back to LIKE scans.
        if _sqlite_has_fts5():
            for stmt in _SQLITE_FTS:
                op.execute(stmt)
    elif dialect == "postgresql":
        for stmt in _POSTGRES_FTS:
            op.execute(stmt)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for name in ("iq_chunks_ai", "iq_chunks_ad", "iq_chunks_au"):
            op.execute(f"DROP TRIGGER IF EXISTS {name}")
        op.execute("DROP TABLE IF EXISTS iq_chunks_fts")
    op.drop_table("iq_dirty")
    op.drop_index(op.f("ix_iq_chunks_section"), table_name="iq_chunks")
    op.drop_index("ix_iq_chunks_embedding_model", table_name="iq_chunks")
    op.drop_index("ix_iq_chunks_container", table_name="iq_chunks")
    op.drop_table("iq_chunks")
