"""Cross-entity search and the IQ retrieve / ask tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from precursor.backend.db import SessionLocal
from precursor.backend.services.links import topic_paths as _topic_paths

if TYPE_CHECKING:
    pass
from precursor.backend.services.mcp.precursor_tools._core import (
    _GATED,
    _MAX_ROWS,
    _iso,
    _section_enabled,
    mcp,
)

# --------------------------------------------------------------------------
# Which accessor tool retrieves the full content behind a search hit, keyed by
# the palette section the hit belongs to. Surfaced on each result so a caller
# knows how to open it.
_ACCESSOR_BY_SECTION = {
    "topics": "get_topic / list_messages",
    "chats": "get_chat / list_chat_messages",
    "agents": "get_agent",
    "live": "get_live_session",
}


@mcp.tool()
async def search(query: str, limit: int = 25) -> dict[str, Any]:
    """Search every surface for ``query`` and return ranked, accessible hits.

    This is the same cross-entity lookup that backs the in-app ⌘K palette. Each
    hit self-describes the ``section`` it belongs to (topics/chats/agents/live),
    the ``field`` that matched, a ``snippet`` around the match, and the container
    ``entity_id`` + ``ref`` — plus an ``accessor`` naming the tool that returns
    its full content.

    Topic hits are always searched. Chat, agent and live hits are only included
    when their own capability section (``chats`` / ``agents`` / ``live``) is also
    exposed, since their snippets disclose that content.

    Topic hits also carry the topic's resolved slug ``path`` (ancestor slugs
    joined root-first), so a caller never has to walk ``parent_id``.
    """
    if not await _section_enabled("search"):
        return {"error": _GATED.format(section="search")}
    if not query.strip():
        return {"error": "query must not be empty"}
    limit = max(1, min(limit, _MAX_ROWS))

    # Surfaces beyond topics only appear when their own section is exposed.
    allowed = {"topics"}
    for section in ("chats", "agents", "live"):
        if await _section_enabled(section):
            allowed.add(section)

    from precursor.backend.services.search import search as run_search

    async with SessionLocal() as session:
        response = await run_search(session, query, limit)
        topic_paths: dict[int, str] = {}
        if any(r.section == "topics" for r in response.results):
            topic_paths = await _topic_paths(session)

    results = []
    for r in response.results:
        if r.section not in allowed:
            continue
        hit: dict[str, Any] = {
            "section": r.section,
            "field": r.field,
            "is_title": r.is_title,
            "entity_id": r.entity_id,
            "ref": r.ref,
            "title": r.title,
            "snippet": r.snippet,
            "role": r.role,
            "updated_at": _iso(r.updated_at),
            "accessor": _ACCESSOR_BY_SECTION.get(r.section),
        }
        if r.section == "topics":
            hit["path"] = topic_paths.get(r.entity_id) or r.ref
        results.append(hit)
    return {"query": query, "results": results, "count": len(results)}


# --------------------------------------------------------------------------
# Precursor IQ (ranked retrieval + cited answers)
# --------------------------------------------------------------------------
async def _iq_gates(sources: list[str] | None) -> set[str] | dict[str, Any]:
    """Exposure sections a caller may retrieve from, narrowed by ``sources``.

    Every hit is gated by its own section (topic metadata and briefs by
    ``topics``, topic conversations by ``messages``, and so on), so IQ never
    reveals more than the per-section tools would.
    """
    from precursor.backend.services.iq.sources import GATE_SECTIONS

    gates = {s for s in GATE_SECTIONS if await _section_enabled(s)}
    if sources:
        unknown = sorted(set(sources) - set(GATE_SECTIONS))
        if unknown:
            return {
                "error": f"Unknown source(s): {', '.join(unknown)}. "
                f"Use any of: {', '.join(GATE_SECTIONS)}."
            }
        gates &= set(sources)
    return gates


@mcp.tool()
async def retrieve(query: str, sources: list[str] | None = None, limit: int = 10) -> dict[str, Any]:
    """Semantic lookup over this Precursor workspace, ready to ground an answer.

    Ranks passages from topics, topic briefs, messages and attachments, chats,
    agent prompts/answers, live meeting notes/summaries/transcripts/insights and
    long-term memory by relevance to a natural-language ``query`` (full-text
    BM25, fused with vector similarity when embeddings are enabled).

    Returns ``markdown`` — numbered excerpts to ground on, citing them inline as
    ``[^n]`` — plus ``hits`` with each passage's ``id`` (its citation number),
    the entity it belongs to (``section``, ``entity_id``, ``ref``, ``path``,
    ``url``), the matched ``field``, a ``snippet`` and the ``accessor`` tool that
    reads the full item.

    ``sources`` narrows the search to exposure sections (``topics``,
    ``messages``, ``chats``, ``agents``, ``live``, ``memory``); hits from a
    section that isn't exposed are never returned. Prefer this over ``search``
    for questions; ``search`` stays a literal substring match.
    """
    if not await _section_enabled("iq"):
        return {"error": _GATED.format(section="iq")}
    if not query.strip():
        return {"error": "query must not be empty"}
    gates = await _iq_gates(sources)
    if isinstance(gates, dict):
        return gates
    from precursor.backend.services.iq.retrieve import MAX_LIMIT
    from precursor.backend.services.iq.retrieve import retrieve as run_retrieve

    result = await run_retrieve(query, gates=gates, limit=max(1, min(limit, MAX_LIMIT)))
    return {
        "query": result.query,
        "markdown": result.markdown,
        "hits": [h.as_dict() for h in result.hits],
        "count": len(result.hits),
        "semantic": result.semantic,
        "pending": result.pending,
    }


@mcp.tool()
async def ask(question: str, sources: list[str] | None = None) -> dict[str, Any]:
    """Answer a question from this Precursor workspace, with citations.

    Retrieves the most relevant passages (see ``retrieve``) and has Precursor's
    configured model write a concise answer grounded only on them, citing each
    as ``[^n]``. Returns ``answer``, the ``citations`` it used and every
    ``sources`` passage it was given. Runs one model call per request.

    ``sources`` narrows grounding to exposure sections, like ``retrieve``.
    """
    if not await _section_enabled("iq_ask"):
        return {"error": _GATED.format(section="iq_ask")}
    if not question.strip():
        return {"error": "question must not be empty"}
    gates = await _iq_gates(sources)
    if isinstance(gates, dict):
        return gates
    from precursor.backend.services.iq.ask import ask as run_ask
    from precursor.backend.services.llm.one_shot import LLMCallFailed

    try:
        result = await run_ask(question, gates=gates)
    except LLMCallFailed as exc:
        return {"error": exc.detail}
    return {
        "question": result.question,
        "answer": result.answer,
        "model": result.model,
        "citations": [h.as_dict() for h in result.citations],
        "sources": [h.as_dict() for h in result.sources],
    }
