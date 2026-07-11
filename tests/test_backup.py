"""Tests for the folder backup service (DB snapshot + blob mirror).

Covers: disabled / unconfigured runs are a no-op ``skipped``; an enabled run
writes a dated DB snapshot and mirrors blob files; the mirror is incremental
(a second run copies nothing new); retention prunes the oldest DB snapshots;
and ``backup_due`` respects the interval and last-run state.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AppSetting
from precursor.backend.services import backup as backup_svc
from precursor.backend.services.backup import backup_due, run_backup


def _init_db() -> None:
    with TestClient(create_app()):
        pass


async def _set(key: str, value: object) -> None:
    async with SessionLocal() as session:
        row = await session.get(AppSetting, key)
        encoded = json.dumps(value)
        if row is None:
            session.add(AppSetting(key=key, value=encoded))
        else:
            row.value = encoded
        await session.commit()


async def _clear_backup_keys() -> None:
    for key in (
        "backup_enabled",
        "backup_dir",
        "backup_retention",
        "backup_last_run_at",
        "backup_last_status",
        "backup_last_error",
    ):
        async with SessionLocal() as session:
            row = await session.get(AppSetting, key)
            if row is not None:
                await session.delete(row)
                await session.commit()


def test_disabled_backup_is_skipped() -> None:
    _init_db()

    async def _run() -> None:
        await _clear_backup_keys()
        result = await run_backup()
        assert not result.ok
        assert result.status == "skipped"

    asyncio.run(_run())


def test_enabled_without_dir_is_skipped() -> None:
    _init_db()

    async def _run() -> None:
        await _clear_backup_keys()
        await _set("backup_enabled", True)
        result = await run_backup()
        assert result.status == "skipped"
        assert "folder" in result.detail.lower()

    asyncio.run(_run())


def test_backup_writes_snapshot_and_mirrors_blobs(tmp_path) -> None:
    _init_db()
    from pathlib import Path

    from precursor.backend.services import blob_store

    # The blob store writes under the conftest-isolated data dir; back that up.
    blobs_root = Path(get_settings().blobs_dir)
    dest = tmp_path / "backup-target"

    sha_a = blob_store.write_blob(b"hello world backup test")
    sha_b = blob_store.write_blob(b"another backup blob")

    async def _run() -> None:
        await _clear_backup_keys()
        await _set("backup_enabled", True)
        await _set("backup_dir", str(dest))
        result = await run_backup()
        assert result.ok, result.detail
        assert result.status == "ok"
        # DB snapshot exists (SQLite test DB) and is a real file.
        assert result.db_snapshot is not None
        snap = list((dest / "db").glob("precursor-*.db"))
        assert len(snap) == 1
        assert snap[0].stat().st_size > 0
        # Both freshly written blobs are present in the mirror.
        assert result.blobs_copied >= 2
        assert result.blobs_total >= 2
        assert result.blobs_total >= result.blobs_copied
        assert (dest / "blobs" / blob_store.blob_path(sha_a).relative_to(blobs_root)).exists()
        assert (dest / "blobs" / blob_store.blob_path(sha_b).relative_to(blobs_root)).exists()

        # Second run: nothing new to mirror (content-addressed dedupe), but the
        # total still reflects everything already present in the mirror.
        second = await run_backup()
        assert second.ok
        assert second.blobs_copied == 0
        assert second.blobs_total == result.blobs_total
        assert "0 new" in second.detail
        assert str(second.blobs_total) in second.detail

    asyncio.run(_run())


def test_retention_prunes_oldest_snapshots(tmp_path) -> None:
    _init_db()
    dest = tmp_path / "backup-target"

    async def _run() -> None:
        await _clear_backup_keys()
        await _set("backup_enabled", True)
        await _set("backup_dir", str(dest))
        await _set("backup_retention", 2)
        # Three runs; only the 2 newest DB snapshots should survive.
        for _ in range(3):
            result = await run_backup()
            assert result.ok
            # Distinct timestamped names require >=1s apart at second precision.
            await asyncio.sleep(1.1)
        snaps = sorted((dest / "db").glob("precursor-*.db"))
        assert len(snaps) == 2

    asyncio.run(_run())


def test_backup_due_respects_interval() -> None:
    _init_db()

    async def _run() -> None:
        await _clear_backup_keys()
        await _set("backup_enabled", True)
        await _set("backup_dir", "/tmp/whatever")

        async with SessionLocal() as session:
            # Never run before → due.
            assert await backup_due(session, 86_400) is True

        # A recent successful run → not due.
        await _set("backup_last_run_at", datetime.now(UTC).isoformat())
        await _set("backup_last_status", "ok")
        async with SessionLocal() as session:
            assert await backup_due(session, 86_400) is False

        # An old successful run → due again.
        old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        await _set("backup_last_run_at", old)
        async with SessionLocal() as session:
            assert await backup_due(session, 86_400) is True

        # Disabled → never due.
        await _set("backup_enabled", False)
        async with SessionLocal() as session:
            assert await backup_due(session, 86_400) is False

    asyncio.run(_run())
    _ = backup_svc  # module import smoke
