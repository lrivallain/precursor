"""Content-addressed on-disk store for attachment bytes.

Attachment payloads used to live as ``LargeBinary`` BLOBs in the main database,
which bloated the SQLite file and made every backup/copy pay for the bytes.
They now live as files under ``<data_dir>/blobs`` keyed by the SHA-256 of their
content, sharded two levels deep by the hash prefix::

    <data_dir>/blobs/<sha[0:2]>/<sha[2:4]>/<sha256>

Sharding keeps any single directory small (uniform hash distribution spreads
files across 65 536 leaf buckets), the same scheme Git uses for its object
store. Content addressing means identical uploads dedupe to one file
automatically — across both the ``attachments`` and ``note_draft_attachments``
tables, since they share this namespace.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from sqlalchemy import select

from precursor.backend.config import get_settings

logger = logging.getLogger(__name__)


def _blobs_root() -> Path:
    return Path(get_settings().blobs_dir)


def blob_path(sha256: str) -> Path:
    """Return the on-disk path for a blob keyed by its SHA-256 hex digest."""
    return _blobs_root() / sha256[:2] / sha256[2:4] / sha256


def write_blob(data: bytes) -> str:
    """Persist ``data`` and return its SHA-256 key.

    Idempotent: identical content maps to the same path, so a repeat upload is a
    no-op. The write goes to a temp file first and is atomically renamed, so a
    concurrent reader never observes a half-written blob.
    """
    sha256 = hashlib.sha256(data).hexdigest()
    dest = blob_path(sha256)
    if dest.exists():
        return sha256
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return sha256


def read_blob(sha256: str) -> bytes:
    """Return the bytes stored under ``sha256`` (raises ``FileNotFoundError``)."""
    return blob_path(sha256).read_bytes()


def blob_exists(sha256: str) -> bool:
    return blob_path(sha256).exists()


def delete_blob(sha256: str) -> None:
    """Remove a blob file if present (best-effort; safe to call on a miss)."""
    blob_path(sha256).unlink(missing_ok=True)


async def gc_orphan_blobs() -> int:
    """Delete blob files no attachment row references. Returns the count removed.

    Row deletes cascade at the DB level (message/topic/chat removal), which the
    ORM can't hook, so blob cleanup can't ride on delete events. Instead we
    sweep: gather every referenced SHA-256 and unlink any file not in the set.
    Best-effort — failures are logged, not raised.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Attachment, MeetingAttachment, NoteDraftAttachment

    root = _blobs_root()
    if not root.exists():
        return 0

    async with SessionLocal() as session:
        referenced: set[str] = set(
            (await session.execute(select(Attachment.sha256))).scalars().all()
        )
        referenced |= set(
            (await session.execute(select(NoteDraftAttachment.sha256))).scalars().all()
        )
        referenced |= set((await session.execute(select(MeetingAttachment.sha256))).scalars().all())

    removed = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.name.endswith(".tmp"):
            continue
        if path.name in referenced:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:  # pragma: no cover - defensive
            logger.warning("Failed to remove orphan blob %s", path, exc_info=True)
    return removed
