"""Built-in MCP server: Precursor's own capabilities (outbound).

Runs as a stdio subprocess (like ``fetch_server`` / ``workspace_fs_server``).
This is how Precursor exposes *itself* over MCP: the same in-tree entrypoint
serves the app's own agent (when the ``precursor`` built-in is enabled) and any
external MCP host that launches::

    python -m precursor.backend.services.mcp.precursor_server

Every tool is gated by a per-section toggle resolved from the DB at call time
(``resolve_mcp_expose``). Nothing is served until the user opts in under
Settings → MCP servers → "Precursor capabilities", because exposing
conversation history / write actions outbound is a deliberate disclosure.

Sections → tools:
- ``topics``       → list_topics, get_topic
- ``messages``     → list_messages
- ``chats``        → list_chats, get_chat, list_chat_messages
- ``agents``       → list_agents, get_agent
- ``live``         → list_live_sessions, get_live_session
- ``search``       → search (cross-entity; chats/agents/live hits are only
                     included when their own section is also exposed)
- ``skills``       → list_skills, get_skill
- ``memory``       → list_memories
- ``memory_write`` → store_memory, update_memory (write — edits long-term memory)
- ``post_message`` → post_message (write — runs a full assistant turn)
- ``schedules``    → list_schedules, get_schedule, create_schedule,
                     set_schedule_enabled, run_schedule_now
- ``reminders``    → list_reminders, get_reminder, set_reminder,
                     cancel_reminder (write — one-shot topic reminders)
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    AgentSession,
    Chat,
    MeetingInsight,
    MeetingSegment,
    MeetingSession,
    Memory,
    Message,
    MessageRole,
    Reminder,
    Topic,
    TopicSchedule,
)
from precursor.backend.services.app_settings import (
    MCP_EXPOSE_SECTIONS,
    resolve_mcp_expose,
    resolve_scheduled_run_timeout_seconds,
)
from precursor.backend.services.schedule_timing import compute_next_run
from precursor.backend.services.slugs import allocate_unique_slug, slugify

# Decorated tool functions are returned unchanged, so the registrar's decorator
# is identity-typed (preserves each tool's signature for callers/mypy).
F = TypeVar("F", bound=Callable[..., Any])


def _transport_security() -> TransportSecuritySettings:
    """Lock the HTTP transport to the app's own loopback host:port.

    DNS-rebinding protection is on by default; the allowlist below is what makes
    the unauthenticated HTTP endpoint safe — it only answers requests whose Host
    header is this instance's localhost bind. Irrelevant to stdio (which has no
    Host header), so it's harmless when the server runs as a subprocess.
    """
    cfg = get_settings()
    port = cfg.port
    return TransportSecuritySettings(
        allowed_hosts=[
            f"127.0.0.1:{port}",
            f"localhost:{port}",
            f"[::1]:{port}",
            "127.0.0.1",
            "localhost",
            "::1",
        ],
        # Host allowlist is the real boundary; local MCP clients send varied
        # (or no) Origin values, so don't reject on Origin.
        allowed_origins=["*"],
    )


class _ToolRegistrar:
    """Collects ``@mcp.tool()``-decorated functions at import time.

    The module decorates tools against this registrar; :func:`build_mcp` then
    stamps them onto a *fresh* ``FastMCP`` instance. A fresh instance per app is
    required because a ``StreamableHTTPSessionManager`` can only be ``run()`` once
    — sharing one instance across app instances (e.g. across tests) would fail.
    """

    def __init__(self) -> None:
        self.tools: list[Callable[..., Any]] = []

    def tool(self, *_args: Any, **_kwargs: Any) -> Callable[[F], F]:
        def deco(fn: F) -> F:
            self.tools.append(fn)
            return fn

        return deco


_registrar = _ToolRegistrar()
# Tools below decorate against the registrar; the stdio FastMCP instance is
# built at the end of the module (``_stdio_mcp``).
mcp = _registrar


def build_mcp() -> FastMCP:
    """Build a fresh FastMCP 'precursor' server with all tools registered."""
    server = FastMCP(
        "precursor",
        # Route path for the streamable-HTTP app. The FastAPI app reuses this
        # route directly (exact ``/mcp``) so the bare URL works without a
        # trailing-slash redirect; see main.create_app.
        streamable_http_path="/mcp",
        transport_security=_transport_security(),
    )
    for fn in _registrar.tools:
        server.tool()(fn)
    return server


# Cap list/search results so a huge instance can't blow the caller's context.
_MAX_ROWS = 200
_GATED = (
    "The '{section}' capability is not exposed by this Precursor instance. "
    "Enable it in Settings → MCP servers → Precursor capabilities."
)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


async def _section_enabled(section: str) -> bool:
    # First-party agent sessions launch this server with full access — the
    # ``mcp_expose`` toggles gate *external* MCP clients, not Precursor's own
    # agents, which must read topic content and post results back.
    if os.environ.get("PRECURSOR_MCP_FULL_ACCESS") == "1":
        return True
    async with SessionLocal() as session:
        expose = await resolve_mcp_expose(session)
    return bool(expose.get(section))


def _topic_dict(t: Topic) -> dict[str, Any]:
    return {
        "id": t.id,
        "slug": t.slug,
        "title": t.title,
        "kind": t.kind,
        "description": t.description,
        "parent_id": t.parent_id,
        "pinned": t.pinned,
        "archived": t.archived_at is not None,
        "created_at": _iso(t.created_at),
        "updated_at": _iso(t.updated_at),
    }


def _message_dict(m: Message) -> dict[str, Any]:
    return {
        "id": m.id,
        "topic_id": m.topic_id,
        "chat_id": m.chat_id,
        "role": m.role.value if hasattr(m.role, "value") else str(m.role),
        "content": m.content,
        "prompt_tokens": m.prompt_tokens,
        "completion_tokens": m.completion_tokens,
        "created_at": _iso(m.created_at),
    }


def _chat_dict(c: Chat) -> dict[str, Any]:
    return {
        "id": c.id,
        "slug": c.slug,
        "title": c.title,
        "description": c.description,
        "pinned": c.pinned,
        "archived": c.archived_at is not None,
        "created_at": _iso(c.created_at),
        "updated_at": _iso(c.updated_at),
    }


def _agent_dict(a: AgentSession) -> dict[str, Any]:
    return {
        "id": a.id,
        "copilot_session_id": a.copilot_session_id,
        "title": a.title,
        "task_prompt": a.task_prompt,
        "status": a.status,
        "result_summary": a.result_summary,
        "error": a.error,
        "model": a.model,
        "topic_id": a.topic_id,
        "chat_id": a.chat_id,
        "archived": a.archived_at is not None,
        "last_activity_at": _iso(a.last_activity_at),
        "created_at": _iso(a.created_at),
        "updated_at": _iso(a.updated_at),
    }


def _live_dict(s: MeetingSession) -> dict[str, Any]:
    return {
        "id": s.id,
        "slug": s.slug,
        "title": s.title,
        "status": s.status,
        "language": s.language,
        "topic_id": s.topic_id,
        "attendees": s.attendees,
        "features": s.features,
        "archived": s.archived_at is not None,
        "started_at": _iso(s.started_at),
        "ended_at": _iso(s.ended_at),
        "created_at": _iso(s.created_at),
        "updated_at": _iso(s.updated_at),
    }


def _schedule_dict(s: TopicSchedule) -> dict[str, Any]:
    return {
        "id": s.id,
        "topic_id": s.topic_id,
        "enabled": s.enabled,
        "prompt": s.prompt,
        "interval_seconds": s.interval_seconds,
        "days_of_week": s.days_of_week,
        "run_at_minute": s.run_at_minute,
        "timezone": s.timezone,
        "clear_context": s.clear_context,
        "status": s.status,
        "next_run_at": _iso(s.next_run_at),
        "last_run_at": _iso(s.last_run_at),
        "last_error": s.last_error,
    }


def _reminder_dict(r: Reminder) -> dict[str, Any]:
    return {
        "id": r.id,
        "topic_id": r.topic_id,
        "chat_id": r.chat_id,
        "remind_at": _iso(r.remind_at),
        "note": r.note,
        "status": r.status,
        "fired_at": _iso(r.fired_at),
    }


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
    }


# --------------------------------------------------------------------------
# topics
# --------------------------------------------------------------------------
@mcp.tool()
async def list_topics(q: str | None = None, include_archived: bool = False) -> dict[str, Any]:
    """List Precursor topics (id, slug, title, kind), optionally filtered by ``q``.

    ``q`` matches the title (case-insensitive). Archived topics are excluded
    unless ``include_archived`` is true.
    """
    if not await _section_enabled("topics"):
        return {"error": _GATED.format(section="topics")}
    async with SessionLocal() as session:
        stmt = select(Topic).order_by(Topic.updated_at.desc()).limit(_MAX_ROWS)
        if not include_archived:
            stmt = stmt.where(Topic.archived_at.is_(None))
        if q:
            stmt = stmt.where(Topic.title.ilike(f"%{q.lower()}%"))
        rows = (await session.execute(stmt)).scalars().all()
    return {"topics": [_topic_dict(t) for t in rows], "count": len(rows)}


@mcp.tool()
async def get_topic(topic_id: int) -> dict[str, Any]:
    """Get a single topic's metadata by id. Use ``list_messages`` for its turns."""
    if not await _section_enabled("topics"):
        return {"error": _GATED.format(section="topics")}
    async with SessionLocal() as session:
        topic = await session.get(Topic, topic_id)
    if topic is None:
        return {"error": f"Topic {topic_id} not found"}
    return _topic_dict(topic)


# --------------------------------------------------------------------------
# messages
# --------------------------------------------------------------------------
@mcp.tool()
async def list_messages(topic_id: int, limit: int = 50) -> dict[str, Any]:
    """List a topic's messages in chronological order (most recent ``limit``).

    Returns user/assistant/system/tool turns with their content and token usage.
    """
    if not await _section_enabled("messages"):
        return {"error": _GATED.format(section="messages")}
    limit = max(1, min(limit, _MAX_ROWS))
    async with SessionLocal() as session:
        if await session.get(Topic, topic_id) is None:
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
    return {"topic_id": topic_id, "messages": [_message_dict(m) for m in rows], "count": len(rows)}


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

    Returns user/assistant/system/tool turns with their content and token usage.
    """
    if not await _section_enabled("chats"):
        return {"error": _GATED.format(section="chats")}
    limit = max(1, min(limit, _MAX_ROWS))
    async with SessionLocal() as session:
        if await session.get(Chat, chat_id) is None:
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
    return {"chat_id": chat_id, "messages": [_message_dict(m) for m in rows], "count": len(rows)}


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

    results = [
        {
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
        for r in response.results
        if r.section in allowed
    ]
    return {"query": query, "results": results, "count": len(results)}


# --------------------------------------------------------------------------
# skills
# --------------------------------------------------------------------------
@mcp.tool()
async def list_skills() -> dict[str, Any]:
    """List Precursor skills (name, description, active) — reusable prompt presets."""
    if not await _section_enabled("skills"):
        return {"error": _GATED.format(section="skills")}
    from precursor.backend.services import skills as skills_service

    async with SessionLocal() as session:
        resolved = await skills_service.reconcile_and_list(session)
    return {
        "skills": [
            {"name": s.name, "description": s.description, "active": s.active} for s in resolved
        ],
        "count": len(resolved),
    }


@mcp.tool()
async def get_skill(name: str) -> dict[str, Any]:
    """Get a skill by name, including its full instructions text."""
    if not await _section_enabled("skills"):
        return {"error": _GATED.format(section="skills")}
    from precursor.backend.services import skills as skills_service

    async with SessionLocal() as session:
        s = await skills_service.get_resolved(session, name)
    if s is None:
        return {"error": f"Skill '{name}' not found"}
    return {
        "name": s.name,
        "description": s.description,
        "instructions": s.instructions,
        "active": s.active,
    }


# --------------------------------------------------------------------------
# memory
# --------------------------------------------------------------------------
@mcp.tool()
async def list_memories() -> dict[str, Any]:
    """List Precursor's long-term memory entries (id, kind, content)."""
    if not await _section_enabled("memory"):
        return {"error": _GATED.format(section="memory")}
    async with SessionLocal() as session:
        rows = (await session.execute(select(Memory).order_by(Memory.id))).scalars().all()
    return {
        "memories": [{"id": m.id, "kind": m.kind, "content": m.content} for m in rows],
        "count": len(rows),
    }


@mcp.tool()
async def store_memory(content: str, kind: str = "context") -> dict[str, Any]:
    """Save a new long-term memory injected into every future conversation.

    ``content`` is the standing fact/preference/context to remember; ``kind`` is a
    short lowercase tag (e.g. "context", "preference", "fact") shown in the UI and
    prepended to the line sent to the model. Returns the created entry.
    """
    if not await _section_enabled("memory_write"):
        return {"error": _GATED.format(section="memory_write")}
    from precursor.backend.schemas import MemoryCreate
    from precursor.backend.services import memories as memory_service

    try:
        payload = MemoryCreate(kind=kind, content=content)
    except ValueError as exc:
        return {"error": str(exc)}
    async with SessionLocal() as session:
        memory = await memory_service.create_memory(session, payload)
        return {"id": memory.id, "kind": memory.kind, "content": memory.content}


@mcp.tool()
async def update_memory(
    memory_id: int, content: str | None = None, kind: str | None = None
) -> dict[str, Any]:
    """Edit an existing long-term memory by id (from ``list_memories``).

    Pass ``content`` and/or ``kind`` to change; omitted fields are left as-is.
    Returns the updated entry, or an error if the id is unknown.
    """
    if not await _section_enabled("memory_write"):
        return {"error": _GATED.format(section="memory_write")}
    from precursor.backend.schemas import MemoryUpdate
    from precursor.backend.services import memories as memory_service

    if content is None and kind is None:
        return {"error": "Provide content and/or kind to update"}
    try:
        payload = MemoryUpdate(content=content, kind=kind)
    except ValueError as exc:
        return {"error": str(exc)}
    async with SessionLocal() as session:
        try:
            memory = await memory_service.update_memory(session, memory_id, payload)
        except LookupError:
            return {"error": f"Memory {memory_id} not found"}
        return {"id": memory.id, "kind": memory.kind, "content": memory.content}


# --------------------------------------------------------------------------
# post_message (write)
# --------------------------------------------------------------------------
@mcp.tool()
async def post_message(topic_id: int, content: str) -> dict[str, Any]:
    """Post a user message to a topic and run a full assistant turn; return the reply.

    This drives a complete generation (system context, history, tool loop) just
    like the chat UI, then returns the assistant's response. The nested turn
    does NOT re-expose Precursor's own MCP tools, so it can't recursively call
    ``post_message``.
    """
    if not await _section_enabled("post_message"):
        return {"error": _GATED.format(section="post_message")}
    if not content.strip():
        return {"error": "content must not be empty"}
    # Imported lazily: pulls in the LLM/MCP stack we don't need for read tools.
    from precursor.backend.services.turn import run_topic_turn_with_timeout

    async with SessionLocal() as session:
        topic = await session.get(Topic, topic_id)
        if topic is None:
            return {"error": f"Topic {topic_id} not found"}
        timeout = await resolve_scheduled_run_timeout_seconds(session)

    try:
        await run_topic_turn_with_timeout(topic_id, content, timeout=float(timeout))
    except TimeoutError:
        return {"error": f"Assistant turn timed out after {timeout}s"}

    async with SessionLocal() as session:
        latest = (
            (
                await session.execute(
                    select(Message)
                    .where(Message.topic_id == topic_id)
                    .where(Message.role == MessageRole.ASSISTANT)
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
    if latest is None:
        return {"topic_id": topic_id, "reply": None, "note": "No assistant reply produced"}
    return {"topic_id": topic_id, "reply": latest.content, "message_id": latest.id}


# --------------------------------------------------------------------------
# schedules
# --------------------------------------------------------------------------
@mcp.tool()
async def list_schedules() -> dict[str, Any]:
    """List recurring scheduled topics and their run state."""
    if not await _section_enabled("schedules"):
        return {"error": _GATED.format(section="schedules")}
    async with SessionLocal() as session:
        rows = (
            (await session.execute(select(TopicSchedule).order_by(TopicSchedule.next_run_at)))
            .scalars()
            .all()
        )
    return {"schedules": [_schedule_dict(s) for s in rows], "count": len(rows)}


@mcp.tool()
async def get_schedule(topic_id: int) -> dict[str, Any]:
    """Get the schedule (recurrence + run state) for a scheduled topic."""
    if not await _section_enabled("schedules"):
        return {"error": _GATED.format(section="schedules")}
    async with SessionLocal() as session:
        s = (
            await session.execute(select(TopicSchedule).where(TopicSchedule.topic_id == topic_id))
        ).scalar_one_or_none()
    if s is None:
        return {"error": f"No schedule for topic {topic_id}"}
    return _schedule_dict(s)


@mcp.tool()
async def create_schedule(
    title: str,
    prompt: str,
    interval_seconds: int,
    days_of_week: int = 127,
    run_at_minute: int | None = None,
    timezone: str = "UTC",
    clear_context: bool = False,
    enabled: bool = True,
) -> dict[str, Any]:
    """Create a recurring schedule on a new topic that runs ``prompt`` on a cadence.

    ``interval_seconds`` is the minimum gap between runs (>= 60).
    ``days_of_week`` is a 7-bit mask (Mon=1 ... Sun=64; 127 = every day).
    ``run_at_minute`` (0-1439) pins a daily time in ``timezone``; omit for a
    pure interval. When ``clear_context`` is set each run starts fresh.
    """
    if not await _section_enabled("schedules"):
        return {"error": _GATED.format(section="schedules")}
    if not title.strip() or not prompt.strip():
        return {"error": "title and prompt must not be empty"}
    if interval_seconds < 60:
        return {"error": "interval_seconds must be >= 60"}
    async with SessionLocal() as session:
        topic = Topic(
            title=title,
            slug=await allocate_unique_slug(session, slugify(title) or "scheduled-topic", Topic),
        )
        session.add(topic)
        await session.flush()
        schedule = TopicSchedule(
            topic_id=topic.id,
            enabled=enabled,
            prompt=prompt,
            interval_seconds=interval_seconds,
            days_of_week=days_of_week,
            run_at_minute=run_at_minute,
            timezone=timezone,
            clear_context=clear_context,
            next_run_at=compute_next_run(
                _now(), interval_seconds, days_of_week, run_at_minute, timezone
            )
            if enabled
            else None,
            status="idle",
        )
        session.add(schedule)
        await session.commit()
        await session.refresh(schedule)
        result = _schedule_dict(schedule)
    return result


@mcp.tool()
async def set_schedule_enabled(topic_id: int, enabled: bool) -> dict[str, Any]:
    """Enable or pause a scheduled topic. Recomputes the next run when enabling."""
    if not await _section_enabled("schedules"):
        return {"error": _GATED.format(section="schedules")}
    async with SessionLocal() as session:
        s = (
            await session.execute(select(TopicSchedule).where(TopicSchedule.topic_id == topic_id))
        ).scalar_one_or_none()
        if s is None:
            return {"error": f"No schedule for topic {topic_id}"}
        s.enabled = enabled
        s.next_run_at = (
            compute_next_run(
                _now(), s.interval_seconds, s.days_of_week, s.run_at_minute, s.timezone
            )
            if enabled
            else None
        )
        await session.commit()
        await session.refresh(s)
        result = _schedule_dict(s)
    return result


@mcp.tool()
async def run_schedule_now(topic_id: int) -> dict[str, Any]:
    """Pull a scheduled topic's next run forward so the ticker fires it soon.

    The background scheduler runs in the main app process and will pick this up
    on its next poll (within the poll interval).
    """
    if not await _section_enabled("schedules"):
        return {"error": _GATED.format(section="schedules")}
    async with SessionLocal() as session:
        s = (
            await session.execute(select(TopicSchedule).where(TopicSchedule.topic_id == topic_id))
        ).scalar_one_or_none()
        if s is None:
            return {"error": f"No schedule for topic {topic_id}"}
        if s.status == "running":
            return {"error": "Run already in progress"}
        s.enabled = True
        s.next_run_at = _now()
        s.status = "idle"
        s.lease_until = None
        s.last_error = None
        await session.commit()
        await session.refresh(s)
        result = _schedule_dict(s)
    return result


# --------------------------------------------------------------------------
# reminders (write — one-shot topic reminders)
# --------------------------------------------------------------------------
def _parse_remind_at(value: str) -> datetime:
    """Parse an ISO 8601 ``remind_at`` string into an aware UTC datetime.

    Accepts a trailing ``Z`` and naive strings (assumed UTC), so callers can
    pass either ``2026-07-20T09:00:00Z`` or ``2026-07-20T09:00:00+02:00``.
    """
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@mcp.tool()
async def list_reminders() -> dict[str, Any]:
    """List one-shot reminders set on topics, soonest first.

    Includes both ``scheduled`` (waiting) and ``fired`` (due, awaiting
    acknowledgment) reminders. Reminders attached to chats are omitted — this
    server is topic-scoped.
    """
    if not await _section_enabled("reminders"):
        return {"error": _GATED.format(section="reminders")}
    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(Reminder)
                    .where(Reminder.topic_id.is_not(None))
                    .order_by(Reminder.remind_at)
                    .limit(_MAX_ROWS)
                )
            )
            .scalars()
            .all()
        )
    return {"reminders": [_reminder_dict(r) for r in rows], "count": len(rows)}


@mcp.tool()
async def get_reminder(topic_id: int) -> dict[str, Any]:
    """Get the one-shot reminder set on a topic, if any."""
    if not await _section_enabled("reminders"):
        return {"error": _GATED.format(section="reminders")}
    from precursor.backend.services import reminders as reminder_service

    async with SessionLocal() as session:
        reminder = await reminder_service.get_reminder(session, "topic", topic_id)
    if reminder is None:
        return {"error": f"No reminder set for topic {topic_id}"}
    return _reminder_dict(reminder)


@mcp.tool()
async def set_reminder(topic_id: int, remind_at: str, note: str | None = None) -> dict[str, Any]:
    """Set (or replace) a one-shot reminder on a topic.

    ``remind_at`` is an ISO 8601 timestamp (e.g. ``2026-07-20T09:00:00Z``);
    naive values are treated as UTC. At that time the topic resurfaces with a
    posted system message. ``note`` is optional free text (max 2000 chars).
    A topic holds at most one reminder, so this replaces any existing one.
    """
    if not await _section_enabled("reminders"):
        return {"error": _GATED.format(section="reminders")}
    from precursor.backend.schemas.reminder import ReminderCreate
    from precursor.backend.services import reminders as reminder_service
    from precursor.backend.services.reminder_ticker import get_reminder_ticker

    try:
        parsed = _parse_remind_at(remind_at)
    except ValueError:
        return {"error": f"remind_at is not a valid ISO 8601 datetime: {remind_at!r}"}
    try:
        payload = ReminderCreate(remind_at=parsed, note=note)
    except ValueError as exc:
        return {"error": str(exc)}
    async with SessionLocal() as session:
        if not await reminder_service.container_exists(session, "topic", topic_id):
            return {"error": f"Topic {topic_id} not found"}
        reminder = await reminder_service.set_reminder(
            session, "topic", topic_id, remind_at=payload.remind_at, note=payload.note
        )
        result = _reminder_dict(reminder)
    # Fire promptly for near/past times instead of waiting for the next poll.
    # A no-op unless the ticker is running in this process (in-app HTTP host).
    await get_reminder_ticker().nudge()
    return result


@mcp.tool()
async def cancel_reminder(topic_id: int) -> dict[str, Any]:
    """Cancel (or acknowledge) a topic's reminder, deleting it.

    Works on both ``scheduled`` and ``fired`` reminders. Returns an error if the
    topic has no reminder.
    """
    if not await _section_enabled("reminders"):
        return {"error": _GATED.format(section="reminders")}
    from precursor.backend.services import reminders as reminder_service

    async with SessionLocal() as session:
        deleted = await reminder_service.delete_reminder(session, "topic", topic_id)
    if not deleted:
        return {"error": f"No reminder set for topic {topic_id}"}
    return {"topic_id": topic_id, "deleted": True}


# Loopback hosts the HTTP transport is allowed to bind to. 0.0.0.0 is *not*
# loopback — an unauthenticated server must never answer on all interfaces.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def is_loopback_host(host: str) -> bool:
    return host in LOOPBACK_HOSTS


def http_endpoint_url() -> str | None:
    """The localhost URL external MCP hosts use, or None when not loopback-bound."""
    cfg = get_settings()
    if not is_loopback_host(cfg.host):
        return None
    return f"http://{cfg.host}:{cfg.port}/mcp"


# Concrete FastMCP instance for the stdio entrypoint. The HTTP transport builds
# its own per-app instance via build_mcp() (session-manager run-once constraint).
_stdio_mcp = build_mcp()


def main() -> None:
    from precursor.backend.logging_config import configure_subprocess_logging

    configure_subprocess_logging()
    _stdio_mcp.run()


if __name__ == "__main__":
    main()
