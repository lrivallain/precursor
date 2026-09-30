"""Hybrid retrieval over the Precursor IQ index.

Two candidate lists are fused with Reciprocal Rank Fusion (RRF):

- **lexical** — BM25 over the full-text index (FTS5 on SQLite, ``tsvector`` on
  Postgres), with a ``LIKE`` fallback when neither exists;
- **semantic** — cosine similarity against chunk vectors, when embeddings are on.

A title hit and recency add small boosts, then hits collapse to the best chunk
per source and are capped per container so one long thread can't crowd out
everything else. The result carries both structured hits and a grounding
Markdown block that numbers each hit as a ``[^n]`` citation.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    AgentSession,
    Chat,
    IQChunk,
    MeetingSession,
    Memory,
    Topic,
)
from precursor.backend.services import links
from precursor.backend.services.iq import indexer
from precursor.backend.services.iq.embeddings import (
    DIMENSIONS,
    as_embedder,
    model_tag,
    normalise,
    vector_cache,
)
from precursor.backend.services.iq.sources import CONTAINER_SECTIONS

_RRF_K = 60
_CANDIDATES = 120
# Below this cosine a vector hit is noise, not a paraphrase.
_MIN_SIMILARITY = 0.2
_TITLE_BONUS = 1.0 / (_RRF_K + 1)
_RECENCY_BONUS = 0.25 / (_RRF_K + 1)
_RECENCY_HALF_LIFE_DAYS = 45.0
_SNIPPET_PAD = 90
# Shorter words match whole-word only: "eur" shouldn't pull in every "Europe".
_PREFIX_MIN = 4
_SNIPPET_LEAD = 40
_EXCERPT_CHARS = 900
MAX_LIMIT = 50

# Minimal EN/FR stop list: dropped from the lexical query unless that would
# leave nothing to search for.
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "de",
        "des",
        "du",
        "en",
        "est",
        "et",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "la",
        "le",
        "les",
        "of",
        "on",
        "or",
        "ou",
        "que",
        "qui",
        "the",
        "this",
        "to",
        "un",
        "une",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
    ]
)

_ACCESSORS = {
    "topics": "get_topic / list_messages",
    "chats": "get_chat / list_chat_messages",
    "agents": "get_agent",
    "live": "get_live_session",
    "memory": "list_memories",
}

_FIELD_LABELS = {
    "title": "title",
    "description": "description",
    "message": "message",
    "prompt": "task prompt",
    "answer": "final answer",
    "notes": "notes",
    "summary": "summary",
    "transcript": "transcript",
    "insight": "insight",
    "brief": "topic brief",
    "memory": "memory",
    "attachment": "attachment",
}

_KIND_LABELS = {
    "topics": "Topic",
    "chats": "Chat",
    "agents": "Agent",
    "live": "Live session",
    "memory": "Memory",
}


def fold(value: str) -> str:
    """Lowercase and strip accents, matching the FTS5 tokenizer's view."""
    decomposed = unicodedata.normalize("NFKD", value.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def query_tokens(query: str) -> list[str]:
    words = [w for w in re.findall(r"\w+", fold(query)) if len(w) >= 2]
    kept = [w for w in words if w not in _STOPWORDS]
    seen: set[str] = set()
    out: list[str] = []
    for word in kept or words:
        if word not in seen:
            seen.add(word)
            out.append(word)
    return out[:16]


@dataclass(slots=True)
class Hit:
    n: int
    section: str
    gate: str
    field: str
    source_kind: str
    source_id: int
    entity_id: int
    ref: str | None
    path: str | None
    title: str
    snippet: str
    excerpt: str
    role: str | None
    is_title: bool
    score: float
    updated_at: datetime | None
    url: str | None
    accessor: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.n,
            "section": self.section,
            "field": self.field,
            "source_kind": self.source_kind,
            "entity_id": self.entity_id,
            "ref": self.ref,
            "path": self.path,
            "title": self.title,
            "snippet": self.snippet,
            "excerpt": self.excerpt,
            "role": self.role,
            "is_title": self.is_title,
            "score": round(self.score, 6),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "url": self.url,
            "accessor": self.accessor,
        }


@dataclass(slots=True)
class RetrieveResult:
    query: str
    hits: list[Hit] = field(default_factory=list)
    markdown: str = ""
    lexical_backend: str = "like"
    semantic: bool = False
    pending: int = 0


# -- candidate generation ---------------------------------------------------


def _section_filter(
    gates: set[str] | None, containers: set[str] | None
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if gates is not None:
        names = [f":g{i}" for i in range(len(gates))]
        clauses.append(f"c.section IN ({', '.join(names)})")
        params.update({f"g{i}": g for i, g in enumerate(sorted(gates))})
    if containers is not None:
        names = [f":k{i}" for i in range(len(containers))]
        clauses.append(f"c.container_kind IN ({', '.join(names)})")
        params.update({f"k{i}": k for i, k in enumerate(sorted(containers))})
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


async def _lexical(
    session: AsyncSession,
    backend: str,
    tokens: list[str],
    gates: set[str] | None,
    containers: set[str] | None,
) -> list[int]:
    if not tokens:
        return []
    where, params = _section_filter(gates, containers)
    params["n"] = _CANDIDATES
    if backend == "fts5":
        match = " OR ".join(f'"{t}"*' if len(t) >= _PREFIX_MIN else f'"{t}"' for t in tokens)
        params["q"] = match
        rows = await session.execute(
            text(
                "SELECT c.id FROM iq_chunks_fts JOIN iq_chunks c ON c.id = iq_chunks_fts.rowid "
                f"WHERE iq_chunks_fts MATCH :q{where} "
                "ORDER BY bm25(iq_chunks_fts, 4.0, 1.0) LIMIT :n"
            ),
            params,
        )
        return [r[0] for r in rows.all()]
    if backend == "tsvector":
        params["q"] = " | ".join(f"{t}:*" if len(t) >= _PREFIX_MIN else t for t in tokens)
        rows = await session.execute(
            text(
                "SELECT c.id FROM iq_chunks c "
                f"WHERE c.text_tsv @@ to_tsquery('simple', :q){where} "
                "ORDER BY ts_rank_cd(c.text_tsv, to_tsquery('simple', :q)) DESC LIMIT :n"
            ),
            params,
        )
        return [r[0] for r in rows.all()]
    # No full-text index: case-insensitive substring per token, ranked by how
    # many tokens a chunk contains. Accent-sensitive, but it always works.
    stmt = select(IQChunk.id, IQChunk.heading, IQChunk.text).where(
        or_(*[IQChunk.text.ilike(f"%{t}%") | IQChunk.heading.ilike(f"%{t}%") for t in tokens])
    )
    if gates is not None:
        stmt = stmt.where(IQChunk.section.in_(gates))
    if containers is not None:
        stmt = stmt.where(IQChunk.container_kind.in_(containers))
    scored: list[tuple[int, int]] = []
    for cid, heading, body in (
        await session.execute(stmt.order_by(IQChunk.id.desc()).limit(_CANDIDATES * 4))
    ).all():
        hay = fold(f"{heading}\n{body}")
        scored.append(
            (sum(hay.count(t) for t in tokens) + 3 * sum(t in fold(heading) for t in tokens), cid)
        )
    scored.sort(reverse=True)
    return [cid for _, cid in scored[:_CANDIDATES]]


async def _semantic(
    session: AsyncSession,
    query: str,
    gates: set[str] | None,
    containers: set[str] | None,
) -> list[int] | None:
    """Vector candidates, or ``None`` when embeddings are off/unavailable."""
    from precursor.backend.services.app_settings import (
        resolve_iq_embedding_model,
        resolve_iq_embeddings_enabled,
    )
    from precursor.backend.services.llm import get_llm_provider

    if not await resolve_iq_embeddings_enabled(session):
        return None
    embedder = as_embedder(await get_llm_provider(session))
    if embedder is None:
        return None
    model = await resolve_iq_embedding_model(session)
    tag = model_tag(embedder.name, model)
    await vector_cache.refresh(session, tag, time.monotonic())
    if not vector_cache.entries:
        return None
    try:
        result = await embedder.embed([query], model=model, dimensions=DIMENSIONS)
    except Exception:
        return None
    if not result.vectors:
        return None
    qvec = normalise(result.vectors[0])
    ranked = await asyncio.to_thread(
        vector_cache.search,
        qvec,
        gates=gates,
        containers=containers,
        limit=_CANDIDATES,
        min_score=_MIN_SIMILARITY,
    )
    return [cid for cid, _ in ranked]


# -- containers -------------------------------------------------------------


@dataclass(slots=True)
class _Container:
    title: str
    ref: str | None
    path: str | None
    url: str | None


async def _containers(
    session: AsyncSession, wanted: dict[str, set[int]]
) -> dict[tuple[str, int], _Container]:
    """Live, non-archived containers; missing/archived ones are simply absent."""
    out: dict[tuple[str, int], _Container] = {}
    if ids := wanted.get("topic"):
        paths = await links.topic_paths(session)
        for t in (
            await session.execute(
                select(Topic).where(Topic.id.in_(ids), Topic.archived_at.is_(None))
            )
        ).scalars():
            path = paths.get(t.id) or t.slug
            out[("topic", t.id)] = _Container(t.title, t.slug, path, links.topic_url(path))
    if ids := wanted.get("chat"):
        for c in (
            await session.execute(select(Chat).where(Chat.id.in_(ids), Chat.archived_at.is_(None)))
        ).scalars():
            out[("chat", c.id)] = _Container(c.title, c.slug, None, links.chat_url(c.slug))
    if ids := wanted.get("agent"):
        for a in (
            await session.execute(
                select(AgentSession).where(
                    AgentSession.id.in_(ids), AgentSession.archived_at.is_(None)
                )
            )
        ).scalars():
            ref = a.public_id or str(a.id)
            out[("agent", a.id)] = _Container(a.title, ref, None, links.agent_url(ref))
    if ids := wanted.get("live"):
        for s in (
            await session.execute(
                select(MeetingSession).where(
                    MeetingSession.id.in_(ids), MeetingSession.archived_at.is_(None)
                )
            )
        ).scalars():
            out[("live", s.id)] = _Container(s.title, s.slug, None, links.live_url(s.slug))
    if ids := wanted.get("memory"):
        for m in (await session.execute(select(Memory).where(Memory.id.in_(ids)))).scalars():
            out[("memory", m.id)] = _Container(f"Memory ({m.kind})", None, None, None)
    return out


# -- presentation -----------------------------------------------------------


def _snippet(body: str, tokens: list[str]) -> str:
    flat = " ".join((body or "").split())
    if not flat:
        return ""
    folded = fold(flat)
    positions = [p for p in (folded.find(t) for t in tokens) if p >= 0]
    if not positions:
        return flat[: _SNIPPET_PAD * 2].strip() + ("…" if len(flat) > _SNIPPET_PAD * 2 else "")
    idx = min(positions)
    # Little lead-in: one-line renderers truncate the tail, so the match has to
    # sit near the start to stay visible.
    start = max(0, idx - _SNIPPET_LEAD)
    if start > 0:
        space = flat.find(" ", start, idx)
        start = space + 1 if space != -1 else start
    end = min(len(flat), idx + _SNIPPET_PAD * 2)
    out = flat[start:end].strip()
    return ("…" if start > 0 else "") + out + ("…" if end < len(flat) else "")


def _excerpt(body: str) -> str:
    body = (body or "").strip()
    if len(body) <= _EXCERPT_CHARS:
        return body
    cut = body.rfind(" ", 0, _EXCERPT_CHARS)
    return body[: cut if cut > 0 else _EXCERPT_CHARS].rstrip() + "…"


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def grounding_markdown(query: str, hits: list[Hit]) -> str:
    """Numbered excerpts a model can ground an answer on and cite as ``[^n]``."""
    if not hits:
        return f'No Precursor content matched "{query}".'
    lines = [
        f'Precursor results for "{query}". Cite a source inline as [^n].',
        "",
    ]
    for hit in hits:
        kind = _KIND_LABELS.get(hit.section, hit.section)
        what = _FIELD_LABELS.get(hit.field, hit.field)
        if hit.role:
            what = f"{hit.role} {what}"
        meta = [what]
        if hit.updated_at:
            meta.append(hit.updated_at.date().isoformat())
        if hit.url:
            meta.append(hit.url)
        lines.append(f'### [^{hit.n}] {kind} "{hit.title}"')
        lines.append(f"_{' · '.join(meta)}_")
        lines.append("")
        lines.append(hit.excerpt or hit.snippet or hit.title)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# -- entry point ------------------------------------------------------------


async def retrieve(
    query: str,
    *,
    gates: set[str] | None = None,
    containers: set[str] | None = None,
    limit: int = 10,
    per_container: int = 3,
    drain_budget: float = 1.0,
) -> RetrieveResult:
    """Rank indexed content for ``query``.

    ``gates`` limits hits to chunks whose MCP exposure section is in the set
    (``None`` = everything, the in-app view). ``containers`` limits the kinds of
    entity returned (e.g. the palette leaves memory out). A bounded drain runs
    first so results reflect writes the background ticker hasn't reached yet.
    """
    query = (query or "").strip()
    result = RetrieveResult(query=query)
    limit = max(1, min(limit, MAX_LIMIT))
    if not query or (gates is not None and not gates):
        return result
    if drain_budget > 0:
        await indexer.drain(max_sources=500, budget_seconds=drain_budget, wait=False)

    tokens = query_tokens(query)
    async with SessionLocal() as session:
        backend = await indexer.lexical_backend(session)
        result.lexical_backend = backend
        lexical = await _lexical(session, backend, tokens, gates, containers)
        semantic = await _semantic(session, query, gates, containers)
        result.semantic = semantic is not None

        fused: dict[int, float] = {}
        for ranked in (lexical, semantic or []):
            for rank, cid in enumerate(ranked):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        if not fused:
            result.pending = await indexer.status_pending(session)
            result.markdown = grounding_markdown(query, [])
            return result

        chunks = {
            c.id: c
            for c in (
                await session.execute(select(IQChunk).where(IQChunk.id.in_(list(fused))))
            ).scalars()
        }
        wanted: dict[str, set[int]] = {}
        for c in chunks.values():
            wanted.setdefault(c.container_kind, set()).add(c.container_id)
        live_containers = await _containers(session, wanted)
        result.pending = await indexer.status_pending(session)

    now = datetime.now(UTC)
    best: dict[tuple[str, int], tuple[float, IQChunk, bool]] = {}
    for cid, score in fused.items():
        chunk = chunks.get(cid)
        if chunk is None or (chunk.container_kind, chunk.container_id) not in live_containers:
            continue
        heading = fold(chunk.heading or "")
        is_title = bool(heading) and all(t in heading for t in tokens)
        if is_title:
            score += _TITLE_BONUS
        updated = _as_utc(chunk.source_updated_at)
        if updated is not None:
            age_days = max(0.0, (now - updated).total_seconds() / 86_400)
            score += _RECENCY_BONUS * math.exp(-age_days / _RECENCY_HALF_LIFE_DAYS)
        key = (chunk.source_kind, chunk.source_id)
        if key not in best or score > best[key][0]:
            best[key] = (score, chunk, is_title)

    ordered = sorted(best.values(), key=lambda item: item[0], reverse=True)
    per: dict[tuple[str, int], int] = {}
    for score, chunk, is_title in ordered:
        ckey = (chunk.container_kind, chunk.container_id)
        if per.get(ckey, 0) >= per_container:
            continue
        per[ckey] = per.get(ckey, 0) + 1
        container = live_containers[ckey]
        section = CONTAINER_SECTIONS.get(chunk.container_kind, chunk.container_kind)
        body = chunk.text or ""
        # A heading-only chunk (a title with no body yet) can only have
        # matched on its title, even when not every query word is in it.
        title_only = not body.strip()
        result.hits.append(
            Hit(
                n=len(result.hits) + 1,
                section=section,
                gate=chunk.section,
                field="title" if is_title or title_only else chunk.field,
                source_kind=chunk.source_kind,
                source_id=chunk.source_id,
                entity_id=chunk.container_id,
                ref=container.ref,
                path=container.path,
                title=container.title,
                snippet=container.title if is_title or title_only else _snippet(body, tokens),
                excerpt=_excerpt(body) or container.title,
                role=chunk.role,
                is_title=is_title,
                score=score,
                updated_at=_as_utc(chunk.source_updated_at),
                url=container.url,
                accessor=_ACCESSORS.get(section),
            )
        )
        if len(result.hits) >= limit:
            break
    result.markdown = grounding_markdown(query, result.hits)
    return result
