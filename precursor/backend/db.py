"""Async SQLAlchemy engine, session factory and FastAPI dependency."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from precursor.backend.config import get_settings
from precursor.backend.services.iq import events as _iq_events

if TYPE_CHECKING:
    from alembic.config import Config

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
)


if engine.dialect.name == "sqlite":

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection: object, _record: object) -> None:
        """Make SQLite tolerate the app's concurrent writers.

        SQLite allows a single writer at a time and, by default, a blocked
        writer fails *immediately* with "database is locked". The agents runtime
        now writes from several coroutines at once (per-event timeline archiving,
        status patches, the permission handler's policy read), so without a busy
        timeout a transient lock can bubble out of, say, the permission handler
        and be turned into an opaque tool denial. WAL + a 5s busy timeout let
        writers queue instead of erroring. No-op on Postgres.
        """
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


# Precursor IQ queues changed rows for re-indexing from a session-wide flush
# hook. Installed here rather than by the IQ package so every process that
# writes through ``SessionLocal`` (the app, the stdio MCP server, scripts)
# keeps the index current, not only the ones that happen to import IQ.
_iq_events.install()

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Bring the database schema to head on startup.

    Alembic migrations are the single source of truth: ``upgrade head`` both
    builds a fresh database and migrates an existing one (additive only —
    existing tables are never dropped or rebuilt). Cases:

    * **Managed at a known revision** → ``upgrade head`` (a no-op when already at
      head; otherwise it applies the pending migrations). The production path.
    * **Managed at a squashed revision** → one of the pre-baseline revisions in
      :data:`_SQUASHED_REVISIONS`. Managed databases were already at head, so the
      live schema matches the baseline — re-adopt it with ``stamp head`` (a
      version-row write only; no schema or data change).
    * **Managed at any other unknown revision** → a newer build migrated this
      database. Refuse to start with :class:`DatabaseNewerThanAppError`, writing
      nothing: stamping it back to this build's head would make the newer build
      replay migrations that are already applied and crash on startup.
    * **Unmanaged** → a fresh database (``upgrade head`` builds it) or a legacy
      ``create_all`` one that has tables but no version row (``stamp head``
      adopts it). Told apart by whether any application table already exists.

    Either way the protected default Assistant Role and Collection are seeded
    (idempotent).
    """
    async with engine.connect() as conn:
        has_version, has_tables, stored = await conn.run_sync(_inspect_alembic_state)

    known = _known_revisions() if has_version else set()
    action, purge = _plan_alembic(has_version, has_tables, stored, known)
    # env.py drives its own asyncio loop, so run Alembic off this one.
    await asyncio.to_thread(_run_alembic, action, "head", purge)

    async with engine.begin() as conn:
        await conn.run_sync(ensure_default_role)
        await conn.run_sync(ensure_default_collection)


# The incremental revisions squashed into ``0001_baseline``. Only these may be
# re-adopted: an unknown revision outside this set is not history this build
# has forgotten but a migration it has never seen.
_SQUASHED_REVISIONS = frozenset(
    {
        "0001_workspaces",
        "0002_scheduled_topics",
        "0003_schedule_days_of_week",
        "0004_schedule_time_of_day",
        "0005_local_workspaces",
        "0006_schedule_clear_context",
        "0007_chat_support",
        "0008_chat_attachments",
        "0009_reminders",
        "0009_usage_records",
        "0010_chat_description_as_system_prompt",
        "0011_roles",
    }
)


class DatabaseNewerThanAppError(RuntimeError):
    """The database was migrated by a newer Precursor build than this one."""

    def __init__(self, revision: str) -> None:
        from precursor import __version__

        self.revision = revision
        super().__init__(
            f"The database is at schema revision {revision!r}, which Precursor "
            f"{__version__} does not know: a newer build has migrated it. Refusing "
            "to start so the database is left untouched. Run a build at least as "
            "new as the one that last used this database (e.g. `precursor service "
            "update`), or point PRECURSOR_DATABASE_URL at another database."
        )


def _plan_alembic(
    has_version: bool, has_tables: bool, stored: str | None, known: set[str]
) -> tuple[str, bool]:
    """Pick the ``(action, purge)`` that brings this database to head."""
    if not has_version:
        # Fresh DB → build from migrations; legacy create_all DB → adopt it.
        return ("stamp", False) if has_tables else ("upgrade", False)
    if stored in known:
        return ("upgrade", False)
    if stored in _SQUASHED_REVISIONS:
        # Purge the stale version row first: stamp can't resolve the orphan.
        return ("stamp", True)
    raise DatabaseNewerThanAppError(str(stored))


def _inspect_alembic_state(sync_conn: Connection) -> tuple[bool, bool, str | None]:
    """Return ``(has_alembic_version, has_app_tables, stored_revision)``."""
    from sqlalchemy import inspect, text

    names = set(inspect(sync_conn).get_table_names())
    has_version = "alembic_version" in names
    has_tables = bool(names - {"alembic_version"})
    stored = None
    if has_version:
        stored = sync_conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    return has_version, has_tables, stored


def _alembic_config() -> Config:
    from alembic.config import Config

    # Wheels omit the CLI's ini. Runtime options are set here and in env.py;
    # naming a missing ini would make fileConfig fail during startup.
    ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini)) if ini.is_file() else Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent / "alembic"))
    return cfg


def _known_revisions() -> set[str]:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    return {rev.revision for rev in script.walk_revisions()}


def _run_alembic(action: str, revision: str, purge: bool = False) -> None:
    """Run an Alembic command against the configured database.

    Executed in a worker thread because Alembic's ``env.py`` calls
    ``asyncio.run``, which can't nest inside the app's running event loop.
    ``purge`` (stamp only) clears the version table first, so a revision
    squashed away by the baseline reset can be re-adopted.
    """
    from alembic import command

    cfg = _alembic_config()
    if action == "upgrade":
        command.upgrade(cfg, revision)
    elif action == "stamp":
        command.stamp(cfg, revision, purge=purge)
    else:  # pragma: no cover - guard
        raise ValueError(f"Unknown alembic action: {action}")


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


def ensure_default_collection(sync_conn: Connection) -> None:
    """Seed the protected default Collection and adopt any orphaned topics.

    Idempotent: runs on every startup so a fresh database — or one created
    before Collections existed — always has somewhere for topics to live. Also
    re-homes topics whose collection was removed out-of-band, since the FK is
    ``SET NULL``.
    """
    from sqlalchemy import inspect, text

    from precursor.backend.models.collection import (
        DEFAULT_COLLECTION_ACCENT,
        DEFAULT_COLLECTION_NAME,
        DEFAULT_COLLECTION_SLUG,
    )

    names = set(inspect(sync_conn).get_table_names())
    if "collections" not in names:
        return
    row = sync_conn.execute(text("SELECT id FROM collections WHERE is_default = 1 LIMIT 1")).first()
    if row is None:
        sync_conn.execute(
            text(
                "INSERT INTO collections "
                "(name, slug, description, github_repo, accent, icon, is_default, "
                " created_at, updated_at) "
                "VALUES (:name, :slug, NULL, NULL, :accent, NULL, 1, "
                " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "name": DEFAULT_COLLECTION_NAME,
                "slug": DEFAULT_COLLECTION_SLUG,
                "accent": DEFAULT_COLLECTION_ACCENT,
            },
        )
        row = sync_conn.execute(
            text("SELECT id FROM collections WHERE is_default = 1 LIMIT 1")
        ).first()
    if row is None or "topics" not in names:
        return
    sync_conn.execute(
        text("UPDATE topics SET collection_id = :cid WHERE collection_id IS NULL"),
        {"cid": row[0]},
    )


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an async session."""
    async with SessionLocal() as session:
        yield session
