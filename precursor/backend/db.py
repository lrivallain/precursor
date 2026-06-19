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
        chat,
        mcp_server,
        memory,
        message,
        reminder,
        role,
        skill,
        topic,
        usage,
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
        # Seed the protected default Assistant Role so discussions always have a
        # fallback persona.
        await conn.run_sync(ensure_default_role)


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
    tables = set(inspector.get_table_names())
    if "messages" in tables:
        cols = {c["name"] for c in inspector.get_columns("messages")}
        if "chat_id" not in cols:
            # Chats reuse the messages table via a nullable chat_id (exactly one
            # of topic_id / chat_id is set). Adding chat_id, dropping topic_id's
            # NOT NULL, and adding the container CHECK all require recreating the
            # table on SQLite — ALTER can't do them. Rebuild + copy existing rows.
            _rebuild_messages_table(sync_conn, source="messages")
        elif "_messages_old" in tables:
            # Finish a rebuild that was interrupted (e.g. an index-name clash on
            # a prior boot): the live rows are still stranded in _messages_old.
            _rebuild_messages_table(sync_conn, source="_messages_old")
        else:
            if "prompt_tokens" not in cols:
                sync_conn.execute(text("ALTER TABLE messages ADD COLUMN prompt_tokens INTEGER"))
            if "completion_tokens" not in cols:
                sync_conn.execute(text("ALTER TABLE messages ADD COLUMN completion_tokens INTEGER"))

    tables = set(inspector.get_table_names())
    if "attachments" in tables:
        cols = {c["name"] for c in inspector.get_columns("attachments")}
        if "chat_id" not in cols:
            # Attachments gained chat support the same way messages did: a
            # nullable chat_id, topic_id relaxed to nullable, and a container
            # CHECK. SQLite can't ALTER those in place, so rebuild + copy.
            _rebuild_attachments_table(sync_conn, source="attachments")
        elif "_attachments_old" in tables:
            _rebuild_attachments_table(sync_conn, source="_attachments_old")

    # Assistant Roles: add the nullable role_id FK to each discussion container.
    # A plain nullable column is an in-place ALTER on SQLite, no rebuild needed.
    for table in ("topics", "chats", "workspaces"):
        if table in inspector.get_table_names():
            cols = {c["name"] for c in inspector.get_columns(table)}
            if "role_id" not in cols:
                sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN role_id INTEGER"))

    # Chat description-as-system-prompt flag (mirrors Alembic 0010). A plain
    # boolean default is an in-place ALTER on SQLite.
    if "chats" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("chats")}
        if "description_as_system_prompt" not in cols:
            sync_conn.execute(
                text(
                    "ALTER TABLE chats ADD COLUMN description_as_system_prompt "
                    "BOOLEAN NOT NULL DEFAULT 0"
                )
            )


def ensure_default_role(sync_conn: Connection) -> None:
    """Seed the protected ``default`` role (empty prompt) if it is missing.

    Idempotent: runs on every startup so a fresh DB — or one created before the
    Roles feature — always has a default to fall back to.
    """
    from sqlalchemy import inspect, text

    if "roles" not in inspect(sync_conn).get_table_names():
        return
    exists = sync_conn.execute(text("SELECT 1 FROM roles WHERE is_default = 1 LIMIT 1")).first()
    if exists:
        return
    sync_conn.execute(
        text(
            "INSERT INTO roles (name, system_prompt, is_default, created_at, updated_at) "
            "VALUES ('default', '', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )


def _rebuild_messages_table(sync_conn: Connection, *, source: str) -> None:
    """Recreate ``messages`` with the current model schema, copying rows over.

    Dev-only. SQLite can't ALTER a column's nullability or add a CHECK in place,
    so we rebuild the table. ``source`` is the table holding the live rows —
    ``messages`` for a first run, or ``_messages_old`` when resuming a rebuild
    that a previous boot left half-finished. Renamed tables keep their old index
    names, so we drop those first to avoid a clash when the fresh table and its
    indexes are created.
    """
    from precursor.backend.models.message import Message

    _rebuild_table(sync_conn, Message.__table__, source=source)


def _rebuild_attachments_table(sync_conn: Connection, *, source: str) -> None:
    """Recreate ``attachments`` with the current model schema, copying rows over.

    Dev-only twin of :func:`_rebuild_messages_table` — see its docstring for the
    rebuild rationale. ``source`` is ``attachments`` on a first run or
    ``_attachments_old`` when resuming an interrupted rebuild.
    """
    from precursor.backend.models.attachment import Attachment

    _rebuild_table(sync_conn, Attachment.__table__, source=source)


def _rebuild_table(sync_conn: Connection, model_table: object, *, source: str) -> None:
    """Rebuild ``model_table``'s table from ``source``, carrying common columns."""
    from sqlalchemy import Table, inspect, text

    assert isinstance(model_table, Table)
    table = model_table.name
    old = f"_{table}_old"

    sync_conn.execute(text("PRAGMA foreign_keys=OFF"))
    if source == table:
        sync_conn.execute(text(f"ALTER TABLE {table} RENAME TO {old}"))
    else:
        # Resuming: drop the empty half-built table so create() can run clean.
        sync_conn.execute(text(f"DROP TABLE IF EXISTS {table}"))

    _drop_user_indexes(sync_conn, old)
    model_table.create(sync_conn)

    old_cols = {c["name"] for c in inspect(sync_conn).get_columns(old)}
    new_cols = {c.name for c in model_table.columns}
    carry = ", ".join(c for c in old_cols if c in new_cols)
    sync_conn.execute(text(f"INSERT INTO {table} ({carry}) SELECT {carry} FROM {old}"))
    sync_conn.execute(text(f"DROP TABLE {old}"))
    sync_conn.execute(text("PRAGMA foreign_keys=ON"))


def _drop_user_indexes(sync_conn: Connection, table: str) -> None:
    """Drop the explicit (non-constraint) indexes attached to ``table``."""
    from sqlalchemy import text

    # ``sql IS NOT NULL`` skips auto indexes backing UNIQUE/PK constraints,
    # which can't be dropped directly.
    rows = sync_conn.execute(
        text(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'index' AND tbl_name = :t AND sql IS NOT NULL"
        ),
        {"t": table},
    ).fetchall()
    for (name,) in rows:
        sync_conn.execute(text(f'DROP INDEX IF EXISTS "{name}"'))


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an async session."""
    async with SessionLocal() as session:
        yield session
