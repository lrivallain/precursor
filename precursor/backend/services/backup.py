"""Folder backup of the SQLite database + content-addressed blob store.

The user points Settings → Backup at a plain directory — typically one synced by
a cloud client such as OneDrive, Dropbox or iCloud Drive — and the app copies a
consistent snapshot there on a daily cadence (see ``services/backup_ticker``).

Why *to* a synced folder rather than running the live DB *in* one: SQLite keeps
the database file open with WAL sidecars while the app runs, and a sync client
copying those mid-write can corrupt them. A backup instead writes a self-
contained snapshot the client can safely upload. Layout under ``<backup_dir>``::

    blobs/<sha[0:2]>/<sha[2:4]>/<sha256>   # incremental mirror of the blob store
    db/precursor-YYYYMMDD-HHMMSS.db        # dated consistent DB snapshots

Blobs are content-addressed and immutable, so the mirror only ever *adds* files
(a repeat run skips everything already there) and is never pruned — keeping
supersets of every blob a past snapshot might reference. DB snapshots are made
with SQLite ``VACUUM INTO``, which reads a consistent image even while the app
is writing, and the newest ``backup_retention`` of them are kept.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.services.app_settings import _get_db_value

logger = logging.getLogger(__name__)

# Retention clamp bounds — a bad value can't wedge the sweep.
_MIN_RETENTION, _MAX_RETENTION = 1, 3650

# AppSetting keys tracking the outcome of the last run (surfaced read-only in
# the Settings UI so the user can confirm backups are actually happening).
_LAST_RUN_KEY = "backup_last_run_at"
_LAST_STATUS_KEY = "backup_last_status"
_LAST_ERROR_KEY = "backup_last_error"


@dataclass(frozen=True)
class BackupConfig:
    enabled: bool
    dir: str
    retention: int


@dataclass(frozen=True)
class BackupResult:
    ok: bool
    # "ok" | "skipped" | "error" — "skipped" means the run couldn't proceed
    # (disabled, no dir, non-sqlite DB) rather than failing outright.
    status: str
    detail: str
    db_snapshot: str | None = None
    blobs_copied: int = 0


async def resolve_backup_enabled(session: AsyncSession) -> bool:
    db_value = await _get_db_value(session, "backup_enabled")
    if isinstance(db_value, bool):
        return db_value
    return get_settings().backup_enabled


async def resolve_backup_dir(session: AsyncSession) -> str:
    db_value = await _get_db_value(session, "backup_dir")
    if isinstance(db_value, str) and db_value.strip():
        return db_value.strip()
    return get_settings().backup_dir.strip()


async def resolve_backup_retention(session: AsyncSession) -> int:
    db_value = await _get_db_value(session, "backup_retention")
    default = get_settings().backup_retention
    if isinstance(db_value, bool) or not isinstance(db_value, (int, float)):
        value = default
    else:
        value = int(db_value)
    return max(_MIN_RETENTION, min(value, _MAX_RETENTION))


async def resolve_backup_config(session: AsyncSession) -> BackupConfig:
    return BackupConfig(
        enabled=await resolve_backup_enabled(session),
        dir=await resolve_backup_dir(session),
        retention=await resolve_backup_retention(session),
    )


async def resolve_backup_status(session: AsyncSession) -> dict[str, Any]:
    """Effective backup settings + last-run state for the Settings UI."""
    cfg = await resolve_backup_config(session)
    return {
        "backup_enabled": cfg.enabled,
        "backup_dir": cfg.dir,
        "backup_retention": cfg.retention,
        "backup_last_run_at": await _get_db_value(session, _LAST_RUN_KEY),
        "backup_last_status": await _get_db_value(session, _LAST_STATUS_KEY),
        "backup_last_error": await _get_db_value(session, _LAST_ERROR_KEY),
    }


def _sqlite_file_path(database_url: str) -> Path | None:
    """Return the on-disk path for a SQLite URL, or ``None`` for other engines.

    Handles the ``sqlite`` / ``sqlite+aiosqlite`` driver forms. An in-memory
    database (``:memory:``) has no file to snapshot, so it maps to ``None`` too.
    """
    from sqlalchemy.engine import make_url

    url = make_url(database_url)
    if not url.get_backend_name().startswith("sqlite"):
        return None
    if not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


def _vacuum_into(src: Path, dest: Path) -> None:
    """Write a consistent snapshot of the ``src`` SQLite file to ``dest``.

    ``VACUUM INTO`` reads a committed, point-in-time image of the database — WAL
    included — so it is safe to call against the live app's database file from a
    separate connection without blocking writers or risking a torn copy.
    """
    conn = sqlite3.connect(str(src))
    try:
        # SQLite rejects VACUUM INTO onto an existing file; the timestamped name
        # makes a collision unlikely, but guard anyway (e.g. two manual runs in
        # the same second).
        dest.unlink(missing_ok=True)
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()


def _mirror_blobs(src_root: Path, dest_root: Path) -> int:
    """Copy blob files missing from the destination mirror. Returns the count.

    Content addressing means a given relative path always holds the same bytes,
    so an existing destination file is never stale — we skip it. ``.tmp`` files
    are in-flight writes from the live store and are ignored.
    """
    if not src_root.exists():
        return 0
    copied = 0
    for path in src_root.rglob("*"):
        if not path.is_file() or path.name.endswith(".tmp"):
            continue
        rel = path.relative_to(src_root)
        dest = dest_root / rel
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        copied += 1
    return copied


def _prune_snapshots(db_dir: Path, retention: int) -> None:
    """Keep the newest ``retention`` dated DB snapshots, delete the rest."""
    snapshots = sorted(
        (p for p in db_dir.glob("precursor-*.db") if p.is_file()),
        key=lambda p: p.name,
        reverse=True,
    )
    for stale in snapshots[retention:]:
        try:
            stale.unlink()
        except OSError:  # pragma: no cover - defensive
            logger.warning("Failed to prune old backup snapshot %s", stale, exc_info=True)


def _run_backup_sync(
    db_file: Path | None,
    blobs_dir: Path,
    dest_dir: Path,
    retention: int,
) -> BackupResult:
    """Blocking backup body — executed off the event loop via ``to_thread``."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    db_snapshot: str | None = None
    if db_file is not None and db_file.exists():
        snap_dir = dest_dir / "db"
        snap_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        snapshot = snap_dir / f"precursor-{stamp}.db"
        _vacuum_into(db_file, snapshot)
        _prune_snapshots(snap_dir, retention)
        db_snapshot = str(snapshot)

    copied = _mirror_blobs(blobs_dir, dest_dir / "blobs")

    if db_snapshot is None:
        detail = f"Blobs mirrored ({copied} new). No SQLite DB file to snapshot."
    else:
        detail = f"DB snapshot written; {copied} new blob(s) mirrored."
    return BackupResult(
        ok=True,
        status="ok",
        detail=detail,
        db_snapshot=db_snapshot,
        blobs_copied=copied,
    )


async def _record_state(status: str, error: str | None) -> None:
    """Persist last-run state so the UI can show whether backups succeed."""
    from precursor.backend.models import AppSetting

    now = datetime.now(UTC).isoformat()
    payload = {
        _LAST_RUN_KEY: now,
        _LAST_STATUS_KEY: status,
        _LAST_ERROR_KEY: error,
    }
    import json

    async with SessionLocal() as session:
        for key, value in payload.items():
            row = await session.get(AppSetting, key)
            encoded = json.dumps(value)
            if row is None:
                session.add(AppSetting(key=key, value=encoded))
            else:
                row.value = encoded
        await session.commit()


async def run_backup(*, record: bool = True) -> BackupResult:
    """Run one backup using the current DB-stored settings.

    Returns a :class:`BackupResult`; a ``skipped`` status means the run was a
    no-op (disabled or misconfigured) rather than a failure. When ``record`` is
    true the outcome is written to the last-run AppSetting keys for the UI.
    """
    async with SessionLocal() as session:
        cfg = await resolve_backup_config(session)

    if not cfg.enabled:
        return BackupResult(ok=False, status="skipped", detail="Backup is disabled.")
    if not cfg.dir:
        return BackupResult(ok=False, status="skipped", detail="No backup folder configured.")

    settings = get_settings()
    db_file = _sqlite_file_path(settings.database_url)
    blobs_dir = Path(settings.blobs_dir)
    dest_dir = Path(cfg.dir).expanduser()

    try:
        result = await asyncio.to_thread(
            _run_backup_sync, db_file, blobs_dir, dest_dir, cfg.retention
        )
    except Exception as exc:
        logger.exception("Backup run failed")
        if record:
            await _record_state("error", str(exc))
        return BackupResult(ok=False, status="error", detail=str(exc))

    if record:
        await _record_state("ok", None)
    logger.info("Backup complete: %s", result.detail)
    return result


async def backup_due(session: AsyncSession, interval_seconds: int) -> bool:
    """True when enabled and no successful backup ran within ``interval_seconds``."""
    if not await resolve_backup_enabled(session):
        return False
    if not await resolve_backup_dir(session):
        return False
    last_run = await _get_db_value(session, _LAST_RUN_KEY)
    last_status = await _get_db_value(session, _LAST_STATUS_KEY)
    if not isinstance(last_run, str) or last_status != "ok":
        return True
    try:
        last = datetime.fromisoformat(last_run)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    elapsed = (datetime.now(UTC) - last).total_seconds()
    return elapsed >= interval_seconds
