"""Enqueue changed sources for re-indexing in the writer's own transaction.

A session-wide ``after_flush`` hook maps every new, changed or deleted ORM
instance of an indexed model to its ``(source_kind, source_id)`` and upserts it
into ``iq_dirty`` on the same connection — so the queue entry commits (or rolls
back) with the write that caused it. The indexer drains the queue later.

Bulk ``update()`` / ``delete()`` statements bypass ORM events; the periodic
reconcile sweep in ``indexer`` catches those.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, bindparam, event, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from precursor.backend.config import get_settings

logger = logging.getLogger(__name__)

# Engines known to carry the ``iq_dirty`` table. Only positives are cached: a
# database that isn't migrated yet is re-checked, and picks enqueuing up once it is.
_READY_ENGINES: set[int] = set()

_UPSERT = text(
    "INSERT INTO iq_dirty (source_kind, source_id, enqueued_at) "
    "VALUES (:kind, :sid, :now) "
    "ON CONFLICT (source_kind, source_id) DO UPDATE SET enqueued_at = excluded.enqueued_at"
).bindparams(bindparam("now", type_=DateTime(timezone=True)))


def _ready(conn: Connection) -> bool:
    key = id(conn.engine)
    if key in _READY_ENGINES:
        return True
    try:
        present = inspect(conn).has_table("iq_dirty")
    except Exception:
        return False
    if present:
        _READY_ENGINES.add(key)
    return present


def _after_flush(session: Session, _flush_context: Any) -> None:
    if not get_settings().iq_enabled:
        return
    # Imported here: ``db`` installs this hook at import time, before the models
    # that ``sources`` needs are guaranteed to be loaded.
    from precursor.backend.services.iq.sources import source_key

    keys: set[tuple[str, int]] = set()
    for bucket in (session.new, session.dirty, session.deleted):
        for obj in bucket:
            key = source_key(obj)
            if key is not None:
                keys.add(key)
    if not keys:
        return
    conn = session.connection()
    if not _ready(conn):
        return
    now = datetime.now(UTC)
    # A savepoint keeps a queue hiccup from poisoning the writer's transaction
    # (Postgres aborts the whole transaction on any failed statement).
    try:
        with conn.begin_nested():
            conn.execute(
                _UPSERT, [{"kind": kind, "sid": sid, "now": now} for kind, sid in sorted(keys)]
            )
    except Exception:
        logger.warning("Precursor IQ: could not enqueue %d change(s)", len(keys), exc_info=True)


_installed = False


def install() -> None:
    """Register the flush hook once per process (idempotent)."""
    global _installed
    if _installed:
        return
    event.listen(Session, "after_flush", _after_flush)
    _installed = True
