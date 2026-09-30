"""What Precursor IQ indexes, and how each source becomes passages.

A *source* is one row that produces searchable text (a topic, a message, a live
transcript, …), addressed by ``(source_kind, source_id)``. Each source belongs
to a *container* — the entity a person navigates to — and carries the MCP
exposure *section* that gates it for external callers.

Archived containers are indexed like any other: retrieval filters them out at
query time, so un-archiving needs no re-index.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import (
    AgentSession,
    Attachment,
    Chat,
    MeetingInsight,
    MeetingSegment,
    MeetingSession,
    Memory,
    Message,
    MessageRole,
    Topic,
    TopicSummary,
)

# Source kinds, in backfill order (cheap, high-signal rows first).
SOURCE_KINDS: tuple[str, ...] = (
    "topic",
    "chat",
    "topic_brief",
    "memory",
    "agent",
    "live",
    "live_insight",
    "message",
    "live_transcript",
    "attachment",
)

# Container kind → the palette/navigation section it opens in.
CONTAINER_SECTIONS: dict[str, str] = {
    "topic": "topics",
    "chat": "chats",
    "agent": "agents",
    "live": "live",
    "memory": "memory",
}

# MCP exposure sections a chunk can be gated by. A topic's own metadata and
# brief ride ``topics``; its conversation (messages + attachments) rides
# ``messages``, mirroring ``get_topic`` vs ``list_messages``.
GATE_SECTIONS: tuple[str, ...] = ("topics", "messages", "chats", "agents", "live", "memory")

_INDEXED_ROLES = (MessageRole.USER, MessageRole.ASSISTANT)

# Passage windowing. Small enough that a hit's excerpt is on-topic, large enough
# that a paragraph keeps its context.
CHUNK_CHARS = 1_200
CHUNK_OVERLAP = 150
# Hard ceiling on text indexed per source, so one pasted log can't dominate.
MAX_SOURCE_CHARS = 120_000


@dataclass(slots=True)
class Passage:
    field: str
    text: str
    heading: str = ""
    role: str | None = None


@dataclass(slots=True)
class SourceDoc:
    kind: str
    source_id: int
    section: str
    container_kind: str
    container_id: int
    updated_at: datetime | None
    passages: list[Passage] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ChunkSpec:
    field: str
    role: str | None
    heading: str
    text: str


def chunk_text(text: str) -> list[str]:
    """Split ``text`` into overlapping windows, preferring natural boundaries."""
    body = re.sub(r"\n{3,}", "\n\n", (text or "").strip())[:MAX_SOURCE_CHARS]
    if not body:
        return []
    if len(body) <= CHUNK_CHARS:
        return [body]
    out: list[str] = []
    pos = 0
    n = len(body)
    while pos < n:
        end = min(n, pos + CHUNK_CHARS)
        if end < n:
            floor = pos + int(CHUNK_CHARS * 0.6)
            for sep in ("\n\n", "\n", ". ", " "):
                cut = body.rfind(sep, floor, end)
                if cut > 0:
                    end = cut + len(sep)
                    break
        piece = body[pos:end].strip()
        if piece:
            out.append(piece)
        if end >= n:
            break
        nxt = max(end - CHUNK_OVERLAP, pos + 1)
        # Start the overlap on a word boundary rather than mid-word.
        space = body.find(" ", nxt, end)
        pos = space + 1 if space != -1 else nxt
    return out


def chunks_for(doc: SourceDoc) -> list[ChunkSpec]:
    """The chunk list a source currently produces, in ``chunk_no`` order."""
    specs: list[ChunkSpec] = []
    for passage in doc.passages:
        pieces = chunk_text(passage.text)
        if not pieces and passage.heading.strip():
            # A title with no body is still findable by its title.
            pieces = [""]
        for i, piece in enumerate(pieces):
            specs.append(
                ChunkSpec(
                    field=passage.field,
                    role=passage.role,
                    heading=passage.heading.strip() if i == 0 else "",
                    text=piece,
                )
            )
    return specs


def _role(role: object) -> str:
    return role.value if hasattr(role, "value") else str(role)


async def _rows(session: AsyncSession, stmt: Select[Any]) -> list[Any]:
    return list((await session.execute(stmt)).scalars().all())


def _speaker_names(raw: str | None) -> dict[str, str]:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items() if v} if isinstance(data, dict) else {}


def _attachment_text(att: Attachment) -> str:
    from precursor.backend.services.attachment_extraction import (
        extract_attachment_text,
        is_image_attachment,
    )

    if is_image_attachment(att):
        return ""
    return extract_attachment_text(att)


def _doc(
    kind: str,
    source_id: int,
    *,
    section: str,
    container: tuple[str, int],
    updated_at: datetime | None,
    passages: list[Passage],
) -> SourceDoc:
    return SourceDoc(
        kind=kind,
        source_id=source_id,
        section=section,
        container_kind=container[0],
        container_id=container[1],
        updated_at=updated_at,
        passages=passages,
    )


async def _load_transcripts(session: AsyncSession, ids: list[int]) -> dict[int, SourceDoc]:
    sessions = {
        s.id: s
        for s in await _rows(session, select(MeetingSession).where(MeetingSession.id.in_(ids)))
    }
    segments = await _rows(
        session,
        select(MeetingSegment)
        .where(MeetingSegment.session_id.in_(list(sessions)))
        .order_by(MeetingSegment.session_id, MeetingSegment.offset_ms, MeetingSegment.id),
    )
    names = {sid: _speaker_names(s.speaker_names_json) for sid, s in sessions.items()}
    lines: dict[int, list[str]] = {}
    latest: dict[int, datetime] = {}
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        label = seg.speaker_label or ""
        speaker = names[seg.session_id].get(label, label)
        lines.setdefault(seg.session_id, []).append(f"{speaker}: {text}" if speaker else text)
        prev = latest.get(seg.session_id)
        if seg.updated_at is not None and (prev is None or seg.updated_at > prev):
            latest[seg.session_id] = seg.updated_at
    return {
        sid: _doc(
            "live_transcript",
            sid,
            section="live",
            container=("live", sid),
            updated_at=latest.get(sid),
            passages=[Passage("transcript", "\n".join(rows))],
        )
        for sid, rows in lines.items()
    }


async def load_sources(
    session: AsyncSession, kind: str, ids: Iterable[int]
) -> dict[int, SourceDoc | None]:
    """Load the current state of ``ids`` of ``kind``; ``None`` = no longer indexable."""
    wanted = sorted(set(ids))
    out: dict[int, SourceDoc | None] = dict.fromkeys(wanted)
    if not wanted:
        return out

    if kind == "topic":
        for t in await _rows(session, select(Topic).where(Topic.id.in_(wanted))):
            out[t.id] = _doc(
                kind,
                t.id,
                section="topics",
                container=("topic", t.id),
                updated_at=t.updated_at,
                passages=[Passage("description", t.description or "", heading=t.title)],
            )
    elif kind == "chat":
        for c in await _rows(session, select(Chat).where(Chat.id.in_(wanted))):
            out[c.id] = _doc(
                kind,
                c.id,
                section="chats",
                container=("chat", c.id),
                updated_at=c.updated_at,
                passages=[Passage("description", c.description or "", heading=c.title)],
            )
    elif kind == "message":
        messages = select(Message).where(Message.id.in_(wanted), Message.role.in_(_INDEXED_ROLES))
        for m in await _rows(session, messages):
            if not (m.content or "").strip():
                continue
            if m.topic_id is not None:
                section, container = "messages", ("topic", m.topic_id)
            elif m.chat_id is not None:
                section, container = "chats", ("chat", m.chat_id)
            else:
                continue
            out[m.id] = _doc(
                kind,
                m.id,
                section=section,
                container=container,
                updated_at=m.updated_at,
                passages=[Passage("message", m.content, role=_role(m.role))],
            )
    elif kind == "topic_brief":
        for s in await _rows(session, select(TopicSummary).where(TopicSummary.id.in_(wanted))):
            if not (s.content or "").strip():
                continue
            out[s.id] = _doc(
                kind,
                s.id,
                section="topics",
                container=("topic", s.topic_id),
                updated_at=s.updated_at,
                passages=[Passage("brief", s.content)],
            )
    elif kind == "agent":
        for a in await _rows(session, select(AgentSession).where(AgentSession.id.in_(wanted))):
            out[a.id] = _doc(
                kind,
                a.id,
                section="agents",
                container=("agent", a.id),
                updated_at=a.updated_at,
                passages=[
                    Passage("prompt", a.task_prompt or "", heading=a.title),
                    Passage("answer", a.result_summary or ""),
                ],
            )
    elif kind == "live":
        stmt = select(MeetingSession).where(MeetingSession.id.in_(wanted))
        for s in await _rows(session, stmt):
            out[s.id] = _doc(
                kind,
                s.id,
                section="live",
                container=("live", s.id),
                updated_at=s.updated_at,
                passages=[
                    Passage("notes", s.notes or "", heading=s.title),
                    Passage("summary", s.summary or ""),
                ],
            )
    elif kind == "live_transcript":
        out.update(await _load_transcripts(session, wanted))
    elif kind == "live_insight":
        insights = select(MeetingInsight).where(MeetingInsight.id.in_(wanted))
        for ins in await _rows(session, insights):
            if not (ins.content or "").strip():
                continue
            out[ins.id] = _doc(
                kind,
                ins.id,
                section="live",
                container=("live", ins.session_id),
                updated_at=ins.updated_at,
                passages=[Passage("insight", f"{ins.kind.replace('_', ' ')}: {ins.content}")],
            )
    elif kind == "memory":
        for mem in await _rows(session, select(Memory).where(Memory.id.in_(wanted))):
            if not (mem.content or "").strip():
                continue
            out[mem.id] = _doc(
                kind,
                mem.id,
                section="memory",
                container=("memory", mem.id),
                updated_at=mem.updated_at,
                passages=[Passage("memory", mem.content, heading=mem.kind or "")],
            )
    elif kind == "attachment":
        attachments = select(Attachment).where(
            Attachment.id.in_(wanted), Attachment.message_id.isnot(None)
        )
        for att in await _rows(session, attachments):
            if att.topic_id is not None:
                section, container = "messages", ("topic", att.topic_id)
            elif att.chat_id is not None:
                section, container = "chats", ("chat", att.chat_id)
            else:
                continue
            # Blob read + PDF/Office parsing is synchronous; keep it off the loop.
            text = await asyncio.to_thread(_attachment_text, att)
            if not text.strip():
                continue
            out[att.id] = _doc(
                kind,
                att.id,
                section=section,
                container=container,
                updated_at=att.updated_at,
                passages=[Passage("attachment", text, heading=att.original_filename or "")],
            )
    return out


def source_id_selects() -> dict[str, Select[Any]]:
    """One ``SELECT id`` per kind listing every row that may produce chunks."""
    return {
        "topic": select(Topic.id).where(true()),
        "chat": select(Chat.id).where(true()),
        "topic_brief": select(TopicSummary.id).where(true()),
        "memory": select(Memory.id).where(true()),
        "agent": select(AgentSession.id).where(true()),
        "live": select(MeetingSession.id).where(true()),
        "live_insight": select(MeetingInsight.id).where(true()),
        "message": select(Message.id).where(Message.role.in_(_INDEXED_ROLES)),
        "live_transcript": select(MeetingSegment.session_id).distinct().where(true()),
        "attachment": select(Attachment.id).where(Attachment.message_id.isnot(None)),
    }


def updated_since_selects(since: datetime) -> dict[str, Select[Any]]:
    """Per kind, the source ids whose rows changed after ``since``."""
    return {
        "topic": select(Topic.id).where(Topic.updated_at > since),
        "chat": select(Chat.id).where(Chat.updated_at > since),
        "topic_brief": select(TopicSummary.id).where(TopicSummary.updated_at > since),
        "memory": select(Memory.id).where(Memory.updated_at > since),
        "agent": select(AgentSession.id).where(AgentSession.updated_at > since),
        "live": select(MeetingSession.id).where(MeetingSession.updated_at > since),
        "live_insight": select(MeetingInsight.id).where(MeetingInsight.updated_at > since),
        "message": select(Message.id).where(
            Message.updated_at > since, Message.role.in_(_INDEXED_ROLES)
        ),
        "live_transcript": select(MeetingSegment.session_id)
        .distinct()
        .where(MeetingSegment.updated_at > since),
        "attachment": select(Attachment.id).where(
            Attachment.updated_at > since, Attachment.message_id.isnot(None)
        ),
    }


# Where each kind's source ids live, for the orphan sweep.
SOURCE_TABLES: dict[str, tuple[str, str]] = {
    "topic": ("topics", "id"),
    "chat": ("chats", "id"),
    "topic_brief": ("topic_summaries", "id"),
    "memory": ("memories", "id"),
    "agent": ("agent_sessions", "id"),
    "live": ("meeting_sessions", "id"),
    "live_insight": ("meeting_insights", "id"),
    "message": ("messages", "id"),
    "live_transcript": ("meeting_segments", "session_id"),
    "attachment": ("attachments", "id"),
}

_KEYED_TYPES: tuple[tuple[type, str], ...] = (
    (Topic, "topic"),
    (Chat, "chat"),
    (Message, "message"),
    (TopicSummary, "topic_brief"),
    (AgentSession, "agent"),
    (MeetingSession, "live"),
    (MeetingInsight, "live_insight"),
    (Memory, "memory"),
    (Attachment, "attachment"),
)


def source_key(obj: object) -> tuple[str, int] | None:
    """``(kind, source_id)`` a changed ORM instance should re-index, if any."""
    if isinstance(obj, MeetingSegment):
        sid = obj.session_id
        return ("live_transcript", sid) if sid is not None else None
    for cls, kind in _KEYED_TYPES:
        if isinstance(obj, cls):
            ident = getattr(obj, "id", None)
            return (kind, ident) if ident is not None else None
    return None
