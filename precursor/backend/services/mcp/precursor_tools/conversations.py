"""Read tools for topics, messages, side chats, agents and Live sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select

from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    AgentSession,
    Chat,
    Collection,
    MeetingInsight,
    MeetingSegment,
    MeetingSession,
    Message,
    Topic,
)
from precursor.backend.services.app_settings import (
    MCP_EXPOSE_SECTIONS,
    resolve_mcp_expose,
)
from precursor.backend.services.links import topic_paths as _topic_paths
from precursor.backend.services.message_links import discussion_path

if TYPE_CHECKING:
    pass
from precursor.backend.services.mcp.precursor_tools._core import (
    _GATED,
    _MAX_ROWS,
    _agent_dict,
    _chat_dict,
    _collection_names,
    _iso,
    _live_dict,
    _message_dict,
    _section_enabled,
    _topic_dict,
    mcp,
)


@mcp.tool()
async def precursor_info() -> dict[str, Any]:
    """Report which Precursor capability sections are exposed over MCP.

    Call this first to discover what this instance allows. Sections that are
    off will reject their tools with an error telling you to enable them.
    """
    async with SessionLocal() as session:
        expose = await resolve_mcp_expose(session)
    from precursor import __version__

    return {
        "name": "precursor",
        "version": __version__,
        "sections": dict(expose),
        "all_sections": list(MCP_EXPOSE_SECTIONS),
        "guidance": {
            "step_plans": (
                "For substantial, multi-stage asks that benefit from distinct work "
                "and review, including several nontrivial deliverables that must agree "
                "on shared timings, identifiers, calculations or constraints, consider "
                "create_step_plan. Automated runs draft and check steps one at a time, not in "
                "parallel, and may pause for approval or review; autonomous filing happens only "
                "if checks pass. If you started a run linked to this conversation, give its "
                "link and end the turn; the app handles the hand-back. Use get_step_plan "
                "when asked for progress or to inspect a plan. Answer a short, "
                "straightforward request directly even if it has several small parts. "
                "Honor an explicit request not to delegate. For a follow-up that "
                "extends an existing result, find and amend the related plan instead."
            ),
        },
    }


# --------------------------------------------------------------------------
# topics
# --------------------------------------------------------------------------
@mcp.tool()
async def list_topics(
    q: str | None = None,
    include_archived: bool = False,
    collection: str | None = None,
) -> dict[str, Any]:
    """List Precursor topics (id, slug, title, kind), optionally filtered by ``q``.

    ``q`` matches the title (case-insensitive). Archived topics are excluded
    unless ``include_archived`` is true. ``collection`` restricts results to a
    single collection, matched on its name or slug (case-insensitive). Each
    topic carries its resolved ``path`` — ancestor slugs joined root-first —
    so there's no need to walk ``parent_id`` yourself.
    """
    if not await _section_enabled("topics"):
        return {"error": _GATED.format(section="topics")}
    async with SessionLocal() as session:
        stmt = select(Topic).order_by(Topic.updated_at.desc()).limit(_MAX_ROWS)
        if not include_archived:
            stmt = stmt.where(Topic.archived_at.is_(None))
        if q:
            stmt = stmt.where(Topic.title.ilike(f"%{q.lower()}%"))
        if collection:
            wanted = collection.strip().lower()
            match = (
                (
                    await session.execute(
                        select(Collection).where(
                            or_(
                                func.lower(Collection.name) == wanted,
                                func.lower(Collection.slug) == wanted,
                            )
                        )
                    )
                )
                .scalars()
                .first()
            )
            if match is None:
                return {"error": f"Collection '{collection}' not found"}
            stmt = stmt.where(Topic.collection_id == match.id)
        rows = (await session.execute(stmt)).scalars().all()
        names = await _collection_names(session)
        paths = await _topic_paths(session)
    return {"topics": [_topic_dict(t, names, paths) for t in rows], "count": len(rows)}


@mcp.tool()
async def get_topic(topic_id: int) -> dict[str, Any]:
    """Get a single topic's metadata by id. Use ``list_messages`` for its turns.

    ``path`` is the topic's resolved slug path (ancestor slugs joined root-first
    with ``/``, ending in its own slug), so callers don't have to follow
    ``parent_id`` upward to build one.
    """
    if not await _section_enabled("topics"):
        return {"error": _GATED.format(section="topics")}
    async with SessionLocal() as session:
        topic = await session.get(Topic, topic_id)
        if topic is None:
            return {"error": f"Topic {topic_id} not found"}
        names = await _collection_names(session)
        paths = await _topic_paths(session)
        data = _topic_dict(topic, names, paths)
        # Side chat titles disclose chat content: listed only with ``chats`` on.
        if await _section_enabled("chats"):
            from precursor.backend.services import side_chats

            items = await side_chats.list_side_chats(session, topic_id)
            data["side_chats"] = [
                {
                    "id": i.id,
                    "title": i.title,
                    "parent_message_id": i.parent_message_id,
                    "message_count": i.message_count,
                    "last_message_at": _iso(i.last_message_at),
                }
                for i in items
            ]
    return data


# --------------------------------------------------------------------------
# messages
# --------------------------------------------------------------------------
@mcp.tool()
async def list_messages(topic_id: int, limit: int = 50) -> dict[str, Any]:
    """List a topic's messages in chronological order (most recent ``limit``).

    Returns turns with their content and token usage. User/assistant turns include
    a ``permalink`` for Markdown references such as ``[Previous reply](permalink)``.
    """
    if not await _section_enabled("messages"):
        return {"error": _GATED.format(section="messages")}
    limit = max(1, min(limit, _MAX_ROWS))
    async with SessionLocal() as session:
        topic = await session.get(Topic, topic_id)
        if topic is None:
            return {"error": f"Topic {topic_id} not found"}
        # Take the newest ``limit`` then present oldest-first.
        rows = (
            (
                await session.execute(
                    select(Message)
                    .where(Message.topic_id == topic_id)
                    .order_by(Message.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    rows = list(reversed(rows))
    path = discussion_path("topic", topic.public_id)
    return {
        "topic_id": topic_id,
        "messages": [_message_dict(m, path) for m in rows],
        "count": len(rows),
    }


# --------------------------------------------------------------------------
# chats
# --------------------------------------------------------------------------
@mcp.tool()
async def list_chats(q: str | None = None, include_archived: bool = False) -> dict[str, Any]:
    """List Precursor chats (id, slug, title), optionally filtered by ``q``.

    Chats are flat conversations (no topic tree / GitHub-issue link). ``q``
    matches the title (case-insensitive); archived chats are excluded unless
    ``include_archived`` is true.
    """
    if not await _section_enabled("chats"):
        return {"error": _GATED.format(section="chats")}
    async with SessionLocal() as session:
        stmt = select(Chat).order_by(Chat.updated_at.desc()).limit(_MAX_ROWS)
        if not include_archived:
            stmt = stmt.where(Chat.archived_at.is_(None))
        if q:
            stmt = stmt.where(Chat.title.ilike(f"%{q.lower()}%"))
        rows = (await session.execute(stmt)).scalars().all()
    return {"chats": [_chat_dict(c) for c in rows], "count": len(rows)}


@mcp.tool()
async def get_chat(chat_id: int) -> dict[str, Any]:
    """Get a single chat's metadata by id. Use ``list_chat_messages`` for turns."""
    if not await _section_enabled("chats"):
        return {"error": _GATED.format(section="chats")}
    async with SessionLocal() as session:
        chat = await session.get(Chat, chat_id)
    if chat is None:
        return {"error": f"Chat {chat_id} not found"}
    return _chat_dict(chat)


@mcp.tool()
async def list_chat_messages(chat_id: int, limit: int = 50) -> dict[str, Any]:
    """List a chat's messages in chronological order (most recent ``limit``).

    Returns turns with their content and token usage. User/assistant turns include
    a ``permalink`` for Markdown references such as ``[Previous reply](permalink)``.
    """
    if not await _section_enabled("chats"):
        return {"error": _GATED.format(section="chats")}
    limit = max(1, min(limit, _MAX_ROWS))
    async with SessionLocal() as session:
        chat = await session.get(Chat, chat_id)
        if chat is None:
            return {"error": f"Chat {chat_id} not found"}
        rows = (
            (
                await session.execute(
                    select(Message)
                    .where(Message.chat_id == chat_id)
                    .order_by(Message.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    rows = list(reversed(rows))
    path = discussion_path("chat", chat.public_id)
    return {
        "chat_id": chat_id,
        "messages": [_message_dict(m, path) for m in rows],
        "count": len(rows),
    }


# --------------------------------------------------------------------------
# agents
# --------------------------------------------------------------------------
@mcp.tool()
async def list_agents(q: str | None = None, include_archived: bool = False) -> dict[str, Any]:
    """List Precursor agent sessions (id, title, status, result summary).

    Agents are long-running Copilot SDK tasks. ``q`` matches the title
    (case-insensitive); archived sessions are excluded unless ``include_archived``
    is true. The live event history is owned by the SDK and not returned here —
    ``task_prompt`` and ``result_summary`` are the durable text on the row.
    """
    if not await _section_enabled("agents"):
        return {"error": _GATED.format(section="agents")}
    async with SessionLocal() as session:
        stmt = select(AgentSession).order_by(AgentSession.updated_at.desc()).limit(_MAX_ROWS)
        if not include_archived:
            stmt = stmt.where(AgentSession.archived_at.is_(None))
        if q:
            stmt = stmt.where(AgentSession.title.ilike(f"%{q.lower()}%"))
        rows = (await session.execute(stmt)).scalars().all()
    return {"agents": [_agent_dict(a) for a in rows], "count": len(rows)}


@mcp.tool()
async def get_agent(agent_id: int) -> dict[str, Any]:
    """Get a single agent session by id: its task prompt, status and final answer.

    ``agent_id`` is the numeric row id (from ``list_agents`` or a search hit's
    ``entity_id``).
    """
    if not await _section_enabled("agents"):
        return {"error": _GATED.format(section="agents")}
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
    if agent is None:
        return {"error": f"Agent {agent_id} not found"}
    return _agent_dict(agent)


# --------------------------------------------------------------------------
# live (meeting sessions)
# --------------------------------------------------------------------------
@mcp.tool()
async def list_live_sessions(
    q: str | None = None, include_archived: bool = False
) -> dict[str, Any]:
    """List Precursor Live (meeting) sessions (id, slug, title, status).

    ``q`` matches the title (case-insensitive); archived sessions are excluded
    unless ``include_archived`` is true. Use ``get_live_session`` for a session's
    notes, summary, transcript and insights.
    """
    if not await _section_enabled("live"):
        return {"error": _GATED.format(section="live")}
    async with SessionLocal() as session:
        stmt = select(MeetingSession).order_by(MeetingSession.updated_at.desc()).limit(_MAX_ROWS)
        if not include_archived:
            stmt = stmt.where(MeetingSession.archived_at.is_(None))
        if q:
            stmt = stmt.where(MeetingSession.title.ilike(f"%{q.lower()}%"))
        rows = (await session.execute(stmt)).scalars().all()
    return {"live_sessions": [_live_dict(s) for s in rows], "count": len(rows)}


@mcp.tool()
async def get_live_session(session_id: int, transcript_limit: int = 100) -> dict[str, Any]:
    """Get a Live session's full content: notes, summary, transcript and insights.

    Returns the session metadata plus its Markdown ``notes``, generated
    ``summary``, the newest ``transcript_limit`` transcript segments (oldest-first),
    and derived insights (action items, decisions, …). ``session_id`` is the
    numeric row id (from ``list_live_sessions`` or a search hit's ``entity_id``).
    """
    if not await _section_enabled("live"):
        return {"error": _GATED.format(section="live")}
    transcript_limit = max(1, min(transcript_limit, _MAX_ROWS))
    async with SessionLocal() as session:
        live = await session.get(MeetingSession, session_id)
        if live is None:
            return {"error": f"Live session {session_id} not found"}
        speaker_names = live.speaker_names
        segments = (
            (
                await session.execute(
                    select(MeetingSegment)
                    .where(MeetingSegment.session_id == session_id)
                    .order_by(MeetingSegment.created_at.desc(), MeetingSegment.id.desc())
                    .limit(transcript_limit)
                )
            )
            .scalars()
            .all()
        )
        insights = (
            (
                await session.execute(
                    select(MeetingInsight)
                    .where(MeetingInsight.session_id == session_id)
                    .order_by(MeetingInsight.created_at, MeetingInsight.id)
                )
            )
            .scalars()
            .all()
        )
    out = _live_dict(live)
    out["notes"] = live.notes
    out["summary"] = live.summary
    out["transcript"] = [
        {
            "id": seg.id,
            "speaker": speaker_names.get(seg.speaker_label or "", seg.speaker_label),
            "text": seg.text,
            "offset_ms": seg.offset_ms,
            "created_at": _iso(seg.created_at),
        }
        for seg in reversed(segments)
    ]
    out["insights"] = [{"id": ins.id, "kind": ins.kind, "content": ins.content} for ins in insights]
    return out
