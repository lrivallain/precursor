"""Cross-entity content search backing the ⌘K palette.

Runs a handful of narrow ``ILIKE`` queries — one per searchable surface — and
folds the hits into a single ranked list. Title/name hits float to the top
(``is_title``), then everything is ordered by recency. Deliberately simple: no
FTS index, just bounded ``LIKE`` scans capped per surface so a big instance
can't blow the response up.

Scope per section:
  * topics — title, description, message content
  * chats  — title, description, message content
  * agents — title, task prompt, final answer (``result_summary``) only
  * live   — title, transcript segments, insights, notes, summary
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from precursor.backend.models.agent_session import AgentSession
from precursor.backend.models.chat import Chat
from precursor.backend.models.meeting import MeetingInsight, MeetingSegment, MeetingSession
from precursor.backend.models.message import Message, MessageRole
from precursor.backend.models.topic import Topic
from precursor.backend.schemas.search import SearchField, SearchResponse, SearchResult

# Upper bound on hits pulled per surface before merging, and on the final list.
# Keeps the palette snappy and the payload small even on huge instances.
_PER_SURFACE = 8
_MAX_RESULTS = 40
# Window of characters kept around a body match for the snippet.
_SNIPPET_PAD = 90


def _snippet(text: str, query: str) -> str:
    """A compact window of ``text`` centred on the first match of ``query``.

    Falls back to the head of the text when the match can't be located (e.g. a
    multi-word ``LIKE`` that spans a boundary the naive find misses).
    """
    body = " ".join((text or "").split())
    if not body:
        return ""
    idx = body.lower().find(query.lower())
    if idx < 0:
        return body[: _SNIPPET_PAD * 2].strip()
    start = max(0, idx - _SNIPPET_PAD)
    end = min(len(body), idx + len(query) + _SNIPPET_PAD)
    out = body[start:end].strip()
    if start > 0:
        out = "…" + out
    if end < len(body):
        out = out + "…"
    return out


def _role_value(role: object) -> str:
    return role.value if hasattr(role, "value") else str(role)


async def search(session: AsyncSession, query: str, limit: int = _MAX_RESULTS) -> SearchResponse:
    """Search every surface for ``query`` and return a ranked, capped list."""
    q = query.strip()
    if not q:
        return SearchResponse(query=query, results=[])
    limit = max(1, min(limit, _MAX_RESULTS))
    like = f"%{q}%"
    results: list[SearchResult] = []

    def add(
        *,
        section: str,
        field: SearchField,
        is_title: bool,
        entity_id: int,
        ref: str | None,
        title: str,
        snippet: str,
        role: str | None = None,
        updated_at: object | None = None,
    ) -> None:
        results.append(
            SearchResult(
                section=section,
                field=field,
                is_title=is_title,
                entity_id=entity_id,
                ref=ref,
                title=title,
                snippet=snippet,
                role=role,
                updated_at=updated_at,
            )
        )

    # -- Topics: title / description ---------------------------------------
    topics = (
        (
            await session.execute(
                select(Topic)
                .where(Topic.archived_at.is_(None))
                .where(Topic.title.ilike(like) | Topic.description.ilike(like))
                .order_by(Topic.updated_at.desc())
                .limit(_PER_SURFACE)
            )
        )
        .scalars()
        .all()
    )
    for t in topics:
        title_hit = q.lower() in (t.title or "").lower()
        add(
            section="topics",
            field="title" if title_hit else "description",
            is_title=title_hit,
            entity_id=t.id,
            ref=t.slug,
            title=t.title,
            snippet=t.title if title_hit else _snippet(t.description or "", q),
            updated_at=t.updated_at,
        )

    # -- Chats: title / description ----------------------------------------
    chats = (
        (
            await session.execute(
                select(Chat)
                .where(Chat.archived_at.is_(None))
                .where(Chat.title.ilike(like) | Chat.description.ilike(like))
                .order_by(Chat.updated_at.desc())
                .limit(_PER_SURFACE)
            )
        )
        .scalars()
        .all()
    )
    for c in chats:
        title_hit = q.lower() in (c.title or "").lower()
        add(
            section="chats",
            field="title" if title_hit else "description",
            is_title=title_hit,
            entity_id=c.id,
            ref=c.slug,
            title=c.title,
            snippet=c.title if title_hit else _snippet(c.description or "", q),
            updated_at=c.updated_at,
        )

    # -- Messages: topic + chat discussion content -------------------------
    # User/assistant turns only; tool/system chatter is noise for a lookup.
    messages = (
        (
            await session.execute(
                select(Message)
                .options(selectinload(Message.topic), selectinload(Message.chat))
                .where(Message.content.ilike(like))
                .where(Message.role.in_([MessageRole.USER, MessageRole.ASSISTANT]))
                .order_by(Message.created_at.desc())
                .limit(_PER_SURFACE * 2)
            )
        )
        .scalars()
        .all()
    )
    for m in messages:
        container = m.topic if m.topic_id is not None else m.chat
        if container is None or getattr(container, "archived_at", None) is not None:
            continue
        add(
            section="topics" if m.topic_id is not None else "chats",
            field="message",
            is_title=False,
            entity_id=container.id,
            ref=container.slug,
            title=container.title,
            snippet=_snippet(m.content or "", q),
            role=_role_value(m.role),
            updated_at=m.created_at,
        )

    # -- Agents: title / prompt / final answer only ------------------------
    agents = (
        (
            await session.execute(
                select(AgentSession)
                .where(AgentSession.archived_at.is_(None))
                .where(
                    AgentSession.title.ilike(like)
                    | AgentSession.task_prompt.ilike(like)
                    | AgentSession.result_summary.ilike(like)
                )
                .order_by(AgentSession.updated_at.desc())
                .limit(_PER_SURFACE)
            )
        )
        .scalars()
        .all()
    )
    for a in agents:
        ref = a.copilot_session_id or str(a.id)
        if q.lower() in (a.title or "").lower():
            field: SearchField = "title"
            is_title = True
            snippet = a.title
        elif q.lower() in (a.task_prompt or "").lower():
            field = "prompt"
            is_title = False
            snippet = _snippet(a.task_prompt or "", q)
        else:
            field = "answer"
            is_title = False
            snippet = _snippet(a.result_summary or "", q)
        add(
            section="agents",
            field=field,
            is_title=is_title,
            entity_id=a.id,
            ref=ref,
            title=a.title,
            snippet=snippet,
            updated_at=a.updated_at,
        )

    # -- Live: title / notes / summary -------------------------------------
    sessions = (
        (
            await session.execute(
                select(MeetingSession)
                .where(MeetingSession.archived_at.is_(None))
                .where(
                    MeetingSession.title.ilike(like)
                    | MeetingSession.notes.ilike(like)
                    | MeetingSession.summary.ilike(like)
                )
                .order_by(MeetingSession.updated_at.desc())
                .limit(_PER_SURFACE)
            )
        )
        .scalars()
        .all()
    )
    for s in sessions:
        if q.lower() in (s.title or "").lower():
            live_field: SearchField = "title"
            is_title = True
            snippet = s.title
        elif q.lower() in (s.notes or "").lower():
            live_field = "notes"
            is_title = False
            snippet = _snippet(s.notes or "", q)
        else:
            live_field = "summary"
            is_title = False
            snippet = _snippet(s.summary or "", q)
        add(
            section="live",
            field=live_field,
            is_title=is_title,
            entity_id=s.id,
            ref=s.slug,
            title=s.title,
            snippet=snippet,
            updated_at=s.updated_at,
        )

    # -- Live: transcript segments -----------------------------------------
    segments = (
        (
            await session.execute(
                select(MeetingSegment)
                .options(selectinload(MeetingSegment.session))
                .where(MeetingSegment.text.ilike(like))
                .order_by(MeetingSegment.created_at.desc())
                .limit(_PER_SURFACE)
            )
        )
        .scalars()
        .all()
    )
    for seg in segments:
        live = seg.session
        if live is None or live.archived_at is not None:
            continue
        add(
            section="live",
            field="transcript",
            is_title=False,
            entity_id=live.id,
            ref=live.slug,
            title=live.title,
            snippet=_snippet(seg.text or "", q),
            updated_at=seg.created_at,
        )

    # -- Live: derived insights --------------------------------------------
    insights = (
        (
            await session.execute(
                select(MeetingInsight)
                .options(selectinload(MeetingInsight.session))
                .where(MeetingInsight.content.ilike(like))
                .order_by(MeetingInsight.created_at.desc())
                .limit(_PER_SURFACE)
            )
        )
        .scalars()
        .all()
    )
    for ins in insights:
        live = ins.session
        if live is None or live.archived_at is not None:
            continue
        add(
            section="live",
            field="insight",
            is_title=False,
            entity_id=live.id,
            ref=live.slug,
            title=live.title,
            snippet=_snippet(ins.content or "", q),
            updated_at=ins.created_at,
        )

    # Title hits first, then most-recent. ``updated_at`` may be null (defensive)
    # so key on a comparable epoch fallback.
    def sort_key(r: SearchResult) -> tuple[int, float]:
        ts = r.updated_at.timestamp() if r.updated_at is not None else 0.0
        return (0 if r.is_title else 1, -ts)

    results.sort(key=sort_key)
    return SearchResponse(query=query, results=results[:limit])
