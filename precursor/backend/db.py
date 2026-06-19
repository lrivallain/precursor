"""Async SQLAlchemy engine, session factory and FastAPI dependency."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

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
    """Bring the database schema up to date on startup.

    Alembic migrations are the single source of truth. Two cases:

    * **Already Alembic-managed** (an ``alembic_version`` row exists) — run
      ``upgrade head``. A no-op when already at head; otherwise it applies the
      pending migrations (additive ALTERs). This is the production path, and it
      never drops or rebuilds existing tables.
    * **Not yet managed** (a fresh DB, or a legacy ``create_all`` one) — build
      the current schema with ``create_all`` and ``stamp`` it at head so future
      migrations apply incrementally. Stamping only writes the version row; it
      never touches table data.

    Either way the protected default Assistant Role is seeded (idempotent).
    """
    # Importing the models package registers every table on ``Base.metadata``.
    from precursor.backend import models  # noqa: F401

    async with engine.connect() as conn:
        managed = await conn.run_sync(_has_alembic_version)

    if managed:
        # env.py drives its own asyncio loop, so run Alembic off this one.
        await asyncio.to_thread(_run_alembic, "upgrade", "head")
    else:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await asyncio.to_thread(_run_alembic, "stamp", "head")

    async with engine.begin() as conn:
        await conn.run_sync(ensure_default_role)


def _has_alembic_version(sync_conn: Connection) -> bool:
    from sqlalchemy import inspect

    return "alembic_version" in inspect(sync_conn).get_table_names()


def _run_alembic(action: str, revision: str) -> None:
    """Run an Alembic command against the configured database.

    Executed in a worker thread because Alembic's ``env.py`` calls
    ``asyncio.run``, which can't nest inside the app's running event loop.
    """
    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "precursor" / "backend" / "alembic"))
    if action == "upgrade":
        command.upgrade(cfg, revision)
    elif action == "stamp":
        command.stamp(cfg, revision)
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
