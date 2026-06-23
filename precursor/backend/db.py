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
    * **Managed at an *unknown* revision** → the stored revision was squashed
      away by a baseline reset. Managed databases were already at head, so the
      live schema matches the new baseline — re-adopt it with ``stamp head`` (a
      version-row write only; no schema or data change).
    * **Unmanaged** → a fresh database (``upgrade head`` builds it) or a legacy
      ``create_all`` one that has tables but no version row (``stamp head``
      adopts it). Told apart by whether any application table already exists.

    Either way the protected default Assistant Role is seeded (idempotent).
    """
    async with engine.connect() as conn:
        has_version, has_tables, stored = await conn.run_sync(_inspect_alembic_state)

    if not has_version:
        # Fresh DB → build from migrations; legacy create_all DB → adopt it.
        action, purge = ("stamp", False) if has_tables else ("upgrade", False)
    elif stored in _known_revisions():
        action, purge = ("upgrade", False)
    else:
        # Stored revision was squashed away by a baseline reset. The live schema
        # already matches the baseline, so re-adopt it — purging the stale
        # version row first, since stamp can't resolve the orphaned revision.
        action, purge = ("stamp", True)
    # env.py drives its own asyncio loop, so run Alembic off this one.
    await asyncio.to_thread(_run_alembic, action, "head", purge)

    async with engine.begin() as conn:
        await conn.run_sync(ensure_default_role)


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

    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "precursor" / "backend" / "alembic"))
    return cfg


def _known_revisions() -> set[str]:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    return {rev.revision for rev in script.walk_revisions()}


def _run_alembic(action: str, revision: str, purge: bool = False) -> None:
    """Run an Alembic command against the configured database.

    Executed in a worker thread because Alembic's ``env.py`` calls
    ``asyncio.run``, which can't nest inside the app's running event loop.
    ``purge`` (stamp only) clears the version table first, so a stale/orphaned
    revision left by a baseline reset can be re-adopted.
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


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an async session."""
    async with SessionLocal() as session:
        yield session
