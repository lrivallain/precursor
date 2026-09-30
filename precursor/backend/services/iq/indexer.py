"""Keep the Precursor IQ index in step with the data it mirrors.

- :func:`drain` turns queued ``iq_dirty`` entries into chunk rows, rewriting only
  chunks whose content changed (so unchanged chunks keep their vectors).
- :func:`ensure_index_version` queues everything once, on first start or when
  the chunking logic changes (``IQ_INDEX_VERSION``).
- :func:`reconcile` is the safety net for writes the flush hook can't see
  (bulk ``update()``/``delete()``, other processes): it re-queues rows changed
  since the last sweep and deletes chunks whose source row is gone.
- :func:`embed_pending` fills in vectors when embeddings are enabled.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, Select, delete, func, literal, or_, select, text, true, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import AppSetting, IQChunk, IQDirty
from precursor.backend.services.iq.embeddings import (
    DIMENSIONS,
    as_embedder,
    model_tag,
    pack,
)
from precursor.backend.services.iq.sources import (
    SOURCE_KINDS,
    SOURCE_TABLES,
    SourceDoc,
    chunks_for,
    load_sources,
    source_id_selects,
    updated_since_selects,
)

logger = logging.getLogger(__name__)

# Bump when chunking or source mapping changes so existing installs re-index.
IQ_INDEX_VERSION = 1
_VERSION_KEY = "iq_index_version"
_RECONCILED_KEY = "iq_reconciled_at"
# Re-queue a little before the watermark: clocks and commit order aren't exact.
_RECONCILE_SLACK = timedelta(minutes=5)
_EMBED_BACKOFF_SECONDS = 300

# One drain at a time per process; concurrent drains would race on the same
# chunk rows. Another process (the stdio MCP server) is handled by catching the
# unique-constraint collision and letting the other writer win.
_drain_lock = asyncio.Lock()
_embed_backoff_until = 0.0
_lexical_cache: dict[str, str] = {}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _dialect(session: AsyncSession) -> str:
    return session.get_bind().dialect.name


async def _get_json(session: AsyncSession, key: str) -> Any:
    row = await session.get(AppSetting, key)
    if row is None:
        return None
    try:
        return json.loads(row.value)
    except (TypeError, ValueError):
        return None


async def _set_json(session: AsyncSession, key: str, value: Any) -> None:
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=json.dumps(value)))
    else:
        row.value = json.dumps(value)


def _dirty_insert(dialect: str) -> Any:
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert(IQDirty)
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    return sqlite_insert(IQDirty)


async def _enqueue_select(session: AsyncSession, kind: str, ids: Select[Any]) -> None:
    sub = ids.subquery()
    src = select(
        literal(kind),
        sub.c[0],
        literal(_utcnow(), type_=DateTime(timezone=True)),
    ).where(true())
    stmt = (
        _dirty_insert(_dialect(session))
        .from_select(["source_kind", "source_id", "enqueued_at"], src)
        .on_conflict_do_nothing()
    )
    await session.execute(stmt)


async def enqueue_all(session: AsyncSession) -> None:
    """Queue every indexable row (caller commits)."""
    for kind, ids in source_id_selects().items():
        await _enqueue_select(session, kind, ids)


async def ensure_index_version() -> bool:
    """Queue a full (re)build when the index is new or its version changed."""
    async with SessionLocal() as session:
        if await _get_json(session, _VERSION_KEY) == IQ_INDEX_VERSION:
            return False
        started = _utcnow()
        await enqueue_all(session)
        await _set_json(session, _VERSION_KEY, IQ_INDEX_VERSION)
        # A full queue covers what the first reconcile sweep would re-queue.
        await _set_json(session, _RECONCILED_KEY, started.isoformat())
        await session.commit()
    logger.info("Precursor IQ: queued a full index build (version %d)", IQ_INDEX_VERSION)
    return True


async def reindex() -> None:
    """Queue every source again (manual "Reindex")."""
    async with SessionLocal() as session:
        await enqueue_all(session)
        await session.commit()


_rebuilds: set[asyncio.Task[None]] = set()
_stopping = False


async def _rebuild() -> None:
    try:
        while not _stopping and await drain(
            max_sources=500, budget_seconds=5.0, keep_going=lambda: not _stopping
        ):
            await asyncio.sleep(0)
        if not _stopping:
            await embed_pending(max_batches=50)
    except Exception:
        logger.exception("Precursor IQ: background rebuild failed")


def spawn_rebuild() -> None:
    """Drain the whole queue in the background (after :func:`reindex`)."""
    global _stopping
    _stopping = False
    task = asyncio.create_task(_rebuild(), name="iq-rebuild")
    _rebuilds.add(task)
    task.add_done_callback(_rebuilds.discard)


async def shutdown(grace_seconds: float = 5.0) -> None:
    """Let background rebuilds reach a commit before the loop goes away."""
    global _stopping
    _stopping = True
    pending = list(_rebuilds)
    if pending:
        _, still = await asyncio.wait(pending, timeout=grace_seconds)
        for task in still:
            task.cancel()


def _hash(field: str, role: str | None, heading: str, body: str) -> str:
    raw = "\x1f".join((field, role or "", heading, body))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _apply(session: AsyncSession, kind: str, sid: int, doc: SourceDoc | None) -> None:
    existing = list(
        (
            await session.execute(
                select(IQChunk)
                .where(IQChunk.source_kind == kind, IQChunk.source_id == sid)
                .order_by(IQChunk.chunk_no)
            )
        )
        .scalars()
        .all()
    )
    specs = chunks_for(doc) if doc is not None else []
    now = _utcnow()
    for i, spec in enumerate(specs):
        digest = _hash(spec.field, spec.role, spec.heading, spec.text)
        assert doc is not None
        if i < len(existing):
            row = existing[i]
            if row.content_hash != digest:
                row.field = spec.field
                row.role = spec.role
                row.heading = spec.heading
                row.text = spec.text
                row.content_hash = digest
                row.embedding = None
                row.embedding_model = None
                row.embedded_at = None
                row.indexed_at = now
            # A message can move containers (e.g. a chat promoted to a topic).
            row.section = doc.section
            row.container_kind = doc.container_kind
            row.container_id = doc.container_id
            row.source_updated_at = doc.updated_at
        else:
            session.add(
                IQChunk(
                    source_kind=kind,
                    source_id=sid,
                    chunk_no=i,
                    section=doc.section,
                    container_kind=doc.container_kind,
                    container_id=doc.container_id,
                    field=spec.field,
                    role=spec.role,
                    heading=spec.heading,
                    text=spec.text,
                    content_hash=digest,
                    source_updated_at=doc.updated_at,
                    indexed_at=now,
                )
            )
    for row in existing[len(specs) :]:
        await session.delete(row)


async def _drain_batch(max_sources: int) -> int:
    async with SessionLocal() as session:
        queued = (
            (
                await session.execute(
                    select(IQDirty).order_by(IQDirty.enqueued_at).limit(max_sources)
                )
            )
            .scalars()
            .all()
        )
        if not queued:
            return 0
        by_kind: dict[str, list[int]] = {}
        for entry in queued:
            by_kind.setdefault(entry.source_kind, []).append(entry.source_id)
        for kind, ids in by_kind.items():
            docs = await load_sources(session, kind, ids) if kind in SOURCE_KINDS else {}
            for sid in ids:
                await _apply(session, kind, sid, docs.get(sid))
        # Only drop the entries this pass read: a write that re-queued a source
        # meanwhile bumped ``enqueued_at`` and must be processed again.
        for entry in queued:
            await session.execute(
                delete(IQDirty).where(
                    IQDirty.source_kind == entry.source_kind,
                    IQDirty.source_id == entry.source_id,
                    IQDirty.enqueued_at <= entry.enqueued_at,
                )
            )
        try:
            await session.commit()
        except IntegrityError:
            # Another process indexed the same source concurrently; its result
            # stands and the queue entry is retried on the next pass.
            await session.rollback()
            return 0
        return len(queued)


async def drain(
    *,
    max_sources: int = 200,
    budget_seconds: float | None = None,
    wait: bool = True,
    keep_going: Callable[[], bool] | None = None,
) -> int:
    """Index queued sources; returns how many were processed.

    Without a budget, one batch of up to ``max_sources`` runs. With
    ``budget_seconds`` the drain works in small batches until the queue is
    empty, ``max_sources`` is reached or time is up. The lock is held per batch,
    so a query-time drain interleaves with the ticker instead of waiting out its
    whole pass; ``wait=False`` gives up once the budget elapses without a turn.
    ``keep_going`` is checked between batches so a shutdown stops at a commit.
    """
    if not get_settings().iq_enabled:
        return 0
    deadline = time.monotonic() + budget_seconds if budget_seconds is not None else None
    total = 0
    while True:
        if wait:
            await _drain_lock.acquire()
        else:
            remaining = (deadline - time.monotonic()) if deadline is not None else 0.0
            try:
                await asyncio.wait_for(_drain_lock.acquire(), timeout=max(0.01, remaining))
            except TimeoutError:
                break
        try:
            size = max_sources if deadline is None else min(50, max_sources - total)
            done = await _drain_batch(size)
        finally:
            _drain_lock.release()
        total += done
        if deadline is None or done == 0 or total >= max_sources or time.monotonic() >= deadline:
            break
        if keep_going is not None and not keep_going():
            break
    return total


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    full: bool
    removed: int


async def reconcile() -> ReconcileResult:
    """Re-queue rows changed since the last sweep and delete orphaned chunks."""
    started = _utcnow()
    async with SessionLocal() as session:
        raw = await _get_json(session, _RECONCILED_KEY)
        since: datetime | None = None
        if isinstance(raw, str):
            try:
                since = datetime.fromisoformat(raw)
            except ValueError:
                since = None
        if since is None:
            await enqueue_all(session)
        else:
            for kind, ids in updated_since_selects(since - _RECONCILE_SLACK).items():
                await _enqueue_select(session, kind, ids)
        removed = 0
        for kind, (table, column) in SOURCE_TABLES.items():
            result = await session.execute(
                text(
                    f"DELETE FROM iq_chunks WHERE source_kind = :kind AND source_id NOT IN "
                    f"(SELECT {column} FROM {table} WHERE {column} IS NOT NULL)"
                ),
                {"kind": kind},
            )
            removed += max(0, int(getattr(result, "rowcount", 0) or 0))
        await _set_json(session, _RECONCILED_KEY, started.isoformat())
        await session.commit()
    return ReconcileResult(full=since is None, removed=removed)


def _embedding_input(heading: str, body: str) -> str:
    return f"{heading}\n{body}".strip() or " "


async def embed_pending(*, batch: int = 64, max_batches: int = 4) -> int:
    """Vectorise chunks that have none for the current model; returns the count."""
    global _embed_backoff_until
    if not get_settings().iq_enabled or time.monotonic() < _embed_backoff_until:
        return 0
    from precursor.backend.services.app_settings import (
        resolve_iq_embedding_model,
        resolve_iq_embeddings_enabled,
    )
    from precursor.backend.services.llm import get_llm_provider
    from precursor.backend.services.usage_stats import record_usage

    async with SessionLocal() as session:
        if not await resolve_iq_embeddings_enabled(session):
            return 0
        embedder = as_embedder(await get_llm_provider(session))
        if embedder is None:
            return 0
        model = await resolve_iq_embedding_model(session)
    tag = model_tag(embedder.name, model)
    done = 0
    for _ in range(max_batches):
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(IQChunk.id, IQChunk.heading, IQChunk.text, IQChunk.content_hash)
                    .where(or_(IQChunk.embedding_model.is_(None), IQChunk.embedding_model != tag))
                    .order_by(IQChunk.id.desc())
                    .limit(batch)
                )
            ).all()
            await session.commit()
            if not rows:
                break
            try:
                result = await embedder.embed(
                    [_embedding_input(h, b) for _, h, b, _ in rows],
                    model=model,
                    dimensions=DIMENSIONS,
                )
            except Exception as exc:
                _embed_backoff_until = time.monotonic() + _EMBED_BACKOFF_SECONDS
                logger.warning("Precursor IQ: embeddings call failed (%s); backing off", exc)
                break
            if len(result.vectors) != len(rows):
                logger.warning("Precursor IQ: embeddings response size mismatch; skipping")
                break
            now = _utcnow()
            for (cid, _, _, digest), vector in zip(rows, result.vectors, strict=True):
                # Guarded on the hash: a chunk rewritten meanwhile must not get
                # the old text's vector stamped with the current model.
                await session.execute(
                    update(IQChunk)
                    .where(IQChunk.id == cid, IQChunk.content_hash == digest)
                    .values(embedding=pack(vector), embedding_model=tag, embedded_at=now)
                )
            await record_usage(
                session,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=0,
                total_tokens=result.prompt_tokens,
                source="/iq-embeddings",
                model=model,
            )
            await session.commit()
            done += len(rows)
    return done


async def lexical_backend(session: AsyncSession) -> str:
    """``fts5`` / ``tsvector`` when the full-text index exists, else ``like``."""
    dialect = _dialect(session)
    cached = _lexical_cache.get(dialect)
    if cached is not None:
        return cached
    backend = "like"
    try:
        if dialect == "sqlite":
            found = await session.scalar(
                text("SELECT 1 FROM sqlite_master WHERE name = 'iq_chunks_fts'")
            )
            backend = "fts5" if found else "like"
        elif dialect == "postgresql":
            found = await session.scalar(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'iq_chunks' AND column_name = 'text_tsv'"
                )
            )
            backend = "tsvector" if found else "like"
    except OperationalError:
        backend = "like"
    if backend != "like":
        _lexical_cache[dialect] = backend
    return backend


async def status_pending(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(IQDirty)) or 0)


async def status() -> dict[str, Any]:
    """Counts and switches for the Settings card and ``/api/iq/status``."""
    from precursor.backend.services.app_settings import (
        resolve_iq_embedding_model,
        resolve_iq_embeddings_enabled,
    )
    from precursor.backend.services.llm import get_llm_provider

    async with SessionLocal() as session:
        chunks = int(await session.scalar(select(func.count()).select_from(IQChunk)) or 0)
        pending = await status_pending(session)
        sections = {
            section: int(count)
            for section, count in (
                await session.execute(
                    select(IQChunk.section, func.count()).group_by(IQChunk.section)
                )
            ).all()
        }
        enabled = await resolve_iq_embeddings_enabled(session)
        model = await resolve_iq_embedding_model(session)
        embedder = as_embedder(await get_llm_provider(session))
        embedded = 0
        if embedder is not None:
            tag = model_tag(embedder.name, model)
            embedded = int(
                await session.scalar(
                    select(func.count()).select_from(IQChunk).where(IQChunk.embedding_model == tag)
                )
                or 0
            )
        reconciled = await _get_json(session, _RECONCILED_KEY)
        backend = await lexical_backend(session)
    return {
        "enabled": get_settings().iq_enabled,
        "chunks": chunks,
        "pending": pending,
        "sections": sections,
        "lexical_backend": backend,
        "embeddings_enabled": enabled,
        "embeddings_available": embedder is not None,
        "embedding_model": model,
        "embedded": embedded,
        "reconciled_at": reconciled if isinstance(reconciled, str) else None,
    }
