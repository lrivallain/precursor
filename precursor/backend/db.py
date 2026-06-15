"""Async SQLAlchemy engine, session factory and FastAPI dependency."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from precursor.backend.config import get_settings
from precursor.backend.models.base import Base

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
)

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Create tables on startup when no migrations are present (dev convenience)."""
    # Import models so they register with the metadata before create_all runs.
    from precursor.backend.models import (  # noqa: F401
        attachment,
        mcp_server,
        memory,
        message,
        skill,
        topic,
        workspace,
    )

    async with engine.begin() as conn:
        # Dev-only: rename legacy tables before create_all so existing data is
        # preserved instead of a fresh empty table being created alongside it.
        # Production should use the equivalent Alembic migration.
        await conn.run_sync(_rename_legacy_tables)
        await conn.run_sync(Base.metadata.create_all)
        # Dev-only: backfill columns added after the DB was first created.
        # create_all does not ALTER existing tables. Production should use Alembic.
        await conn.run_sync(_ensure_dev_columns)


def _rename_legacy_tables(sync_conn: Connection) -> None:
    from sqlalchemy import inspect, text

    tables = set(inspect(sync_conn).get_table_names())
    if "knowledge_areas" in tables and "workspaces" not in tables:
        sync_conn.execute(text("ALTER TABLE knowledge_areas RENAME TO workspaces"))


def _ensure_dev_columns(sync_conn: Connection) -> None:
    from sqlalchemy import inspect, text

    from precursor.backend.services.slugs import slugify

    inspector = inspect(sync_conn)
    if "topics" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("topics")}
        if "last_read_at" not in cols:
            sync_conn.execute(text("ALTER TABLE topics ADD COLUMN last_read_at TIMESTAMP"))
        if "pinned" not in cols:
            sync_conn.execute(
                text("ALTER TABLE topics ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT 0")
            )
        if "archived_at" not in cols:
            sync_conn.execute(text("ALTER TABLE topics ADD COLUMN archived_at TIMESTAMP"))
        if "slug" not in cols:
            sync_conn.execute(text("ALTER TABLE topics ADD COLUMN slug VARCHAR(255)"))
            # Backfill: assign each existing row a unique slug derived from its
            # title (fall back to `topic-<id>` for empty/non-ASCII-only titles).
            rows = sync_conn.execute(text("SELECT id, title FROM topics ORDER BY id")).fetchall()
            used: set[str] = set()
            for row in rows:
                base = slugify(row.title) or f"topic-{row.id}"
                candidate = base
                n = 2
                while candidate in used:
                    candidate = f"{base}-{n}"
                    n += 1
                used.add(candidate)
                sync_conn.execute(
                    text("UPDATE topics SET slug = :s WHERE id = :i"),
                    {"s": candidate, "i": row.id},
                )
            sync_conn.execute(
                text("CREATE UNIQUE INDEX IF NOT EXISTS ix_topics_slug ON topics(slug)")
            )
    if "messages" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("messages")}
        if "prompt_tokens" not in cols:
            sync_conn.execute(text("ALTER TABLE messages ADD COLUMN prompt_tokens INTEGER"))
        if "completion_tokens" not in cols:
            sync_conn.execute(text("ALTER TABLE messages ADD COLUMN completion_tokens INTEGER"))


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an async session."""
    async with SessionLocal() as session:
        yield session
