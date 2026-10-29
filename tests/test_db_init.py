"""How ``init_db`` treats the Alembic revision it finds on startup.

The costly mistake is an older build meeting a database a newer build migrated:
re-stamping it to the older head rewinds the version row without touching the
schema, and the newer build then replays applied migrations and crash-loops on
"duplicate column".
"""

from __future__ import annotations

import os
import tempfile

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from precursor.backend import db


def test_fresh_database_is_built_from_migrations() -> None:
    assert db._plan_alembic(False, False, None, set()) == ("upgrade", False)


def test_legacy_create_all_database_is_adopted() -> None:
    assert db._plan_alembic(False, True, None, set()) == ("stamp", False)


def test_known_revision_is_upgraded() -> None:
    assert db._plan_alembic(True, True, "abc", {"abc"}) == ("upgrade", False)


@pytest.mark.parametrize("revision", sorted(db._SQUASHED_REVISIONS))
def test_squashed_revision_is_readopted(revision: str) -> None:
    assert db._plan_alembic(True, True, revision, {"0001_baseline"}) == ("stamp", True)


def test_revision_from_a_newer_build_refuses_to_start() -> None:
    with pytest.raises(db.DatabaseNewerThanAppError, match="c448f583b9d6"):
        db._plan_alembic(True, True, "c448f583b9d6", {"0001_baseline", "f1e3c30b32f5"})


def test_squashed_revisions_are_really_gone() -> None:
    # A live revision in the squashed set would be purge-stamped instead of upgraded.
    assert not db._SQUASHED_REVISIONS & db._known_revisions()


async def test_init_db_leaves_a_newer_database_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    newer = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with newer.begin() as conn:
            await conn.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
            await conn.execute(sa.text("INSERT INTO alembic_version VALUES ('ffffffffffff')"))
            await conn.execute(sa.text("CREATE TABLE topics (id INTEGER PRIMARY KEY)"))
        monkeypatch.setattr(db, "engine", newer)

        def _no_alembic(*_args: object) -> None:
            raise AssertionError("Alembic must not run against a newer database")

        monkeypatch.setattr(db, "_run_alembic", _no_alembic)

        with pytest.raises(db.DatabaseNewerThanAppError):
            await db.init_db()

        async with newer.connect() as conn:
            stored = (await conn.execute(sa.text("SELECT version_num FROM alembic_version"))).all()
        assert stored == [("ffffffffffff",)]
    finally:
        await newer.dispose()
        os.unlink(path)
