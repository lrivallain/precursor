"""Optional dense vectors for Precursor IQ.

Embeddings are off by default (they send every indexed passage to the active
provider). When on, each chunk gets a small normalised float32 vector; retrieval
compares the query vector against all of them in pure Python — at personal scale
(tens of thousands of chunks) a 256-dimension dot product per chunk is fast
enough that no vector extension or numpy dependency is needed.
"""

from __future__ import annotations

import math
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from operator import mul
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import IQChunk
from precursor.backend.services.llm.base import EmbeddingResult

# Requested vector size. ``text-embedding-3-*`` shortens natively to any size;
# models that can't are retried without it and keep their native width.
DIMENSIONS = 256


@runtime_checkable
class Embedder(Protocol):
    name: str

    async def embed(
        self, texts: Sequence[str], *, model: str, dimensions: int | None = None
    ) -> EmbeddingResult: ...


def as_embedder(provider: object) -> Embedder | None:
    return provider if isinstance(provider, Embedder) else None


async def resolve_embedder(
    session: AsyncSession, provider_id: str | None = None
) -> Embedder | None:
    """The provider that vectorises passages.

    ``provider_id`` defaults to the ``iq_embedding_provider`` setting; ``""``
    follows the global default provider. A named provider that isn't configured
    yields ``None`` (full-text only) rather than silently embedding elsewhere:
    its vectors would carry another provider's tag.
    """
    from precursor.backend.services.app_settings import resolve_iq_embedding_provider
    from precursor.backend.services.llm import ProviderConfigurationError, get_llm_provider

    chosen = await resolve_iq_embedding_provider(session) if provider_id is None else provider_id
    if not chosen:
        return as_embedder(await get_llm_provider(session))
    try:
        return as_embedder(await get_llm_provider(session, override_provider=chosen, strict=True))
    except ProviderConfigurationError:
        return None


def model_tag(provider_name: str, model: str) -> str:
    """Identity of a vector space: switching provider or model invalidates vectors."""
    return f"{provider_name}:{model}"


def pack(vector: Sequence[float]) -> bytes:
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return array("f", (v / norm for v in vector)).tobytes()


def unpack(blob: bytes) -> array[float]:
    out = array("f")
    out.frombytes(blob)
    return out


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(map(mul, a, b)) if len(a) == len(b) else 0.0


def normalise(vector: Sequence[float]) -> array[float]:
    return unpack(pack(vector))


@dataclass(slots=True)
class _Entry:
    vector: array[float]
    section: str
    container_kind: str


class VectorCache:
    """In-process copy of every chunk vector for one model tag.

    Refreshed incrementally from ``embedded_at``; deleted chunks linger until the
    next full reload, which is harmless because retrieval re-loads its candidate
    rows and drops ids that no longer exist.
    """

    FULL_RELOAD_SECONDS = 600

    def __init__(self) -> None:
        self.tag: str | None = None
        self.entries: dict[int, _Entry] = {}
        self.watermark: datetime | None = None
        self.loaded_at: float = 0.0

    def reset(self, tag: str | None = None) -> None:
        self.tag = tag
        self.entries = {}
        self.watermark = None
        self.loaded_at = 0.0

    async def refresh(self, session: AsyncSession, tag: str, now: float) -> None:
        if tag != self.tag or now - self.loaded_at > self.FULL_RELOAD_SECONDS:
            self.reset(tag)
            self.loaded_at = now
        stmt = select(
            IQChunk.id,
            IQChunk.embedding,
            IQChunk.section,
            IQChunk.container_kind,
            IQChunk.embedded_at,
        ).where(IQChunk.embedding_model == tag, IQChunk.embedding.isnot(None))
        if self.watermark is not None:
            stmt = stmt.where(IQChunk.embedded_at >= self.watermark)
        for cid, blob, section, ckind, embedded_at in (await session.execute(stmt)).all():
            self.entries[cid] = _Entry(unpack(blob), section, ckind)
            if embedded_at is not None and (self.watermark is None or embedded_at > self.watermark):
                self.watermark = embedded_at

    def search(
        self,
        query: Sequence[float],
        *,
        gates: set[str] | None,
        containers: set[str] | None,
        limit: int,
        min_score: float,
    ) -> list[tuple[int, float]]:
        scored: list[tuple[int, float]] = []
        for cid, entry in self.entries.items():
            if gates is not None and entry.section not in gates:
                continue
            if containers is not None and entry.container_kind not in containers:
                continue
            score = dot(query, entry.vector)
            if score >= min_score:
                scored.append((cid, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]


vector_cache = VectorCache()
