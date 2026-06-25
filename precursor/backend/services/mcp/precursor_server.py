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
- ``search``       → search
- ``skills``       → list_skills, get_skill
- ``memory``       → list_memories
- ``memory_write`` → store_memory, update_memory (write — edits long-term memory)
- ``post_message`` → post_message (write — runs a full assistant turn)
- ``schedules``    → list_schedules, get_schedule, create_schedule,
                     set_schedule_enabled, run_schedule_now
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    Memory,
    Message,
    MessageRole,
    Skill,
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
        "role": m.role.value if hasattr(m.role, "value") else str(m.role),
        "content": m.content,
        "prompt_tokens": m.prompt_tokens,
        "completion_tokens": m.completion_tokens,
        "created_at": _iso(m.created_at),
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
# search
# --------------------------------------------------------------------------
@mcp.tool()
async def search(query: str, limit: int = 25) -> dict[str, Any]:
    """Search across topic titles and message content for ``query``.

    Returns matching topics and message snippets (with their topic id) so a
    caller can locate relevant conversations.
    """
    if not await _section_enabled("search"):
        return {"error": _GATED.format(section="search")}
    if not query.strip():
        return {"error": "query must not be empty"}
    limit = max(1, min(limit, _MAX_ROWS))
    like = f"%{query.lower()}%"
    async with SessionLocal() as session:
        topics = (
            (
                await session.execute(
                    select(Topic)
                    .where(Topic.archived_at.is_(None))
                    .where(Topic.title.ilike(like))
                    .order_by(Topic.updated_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        messages = (
            (
                await session.execute(
                    select(Message)
                    .where(Message.content.ilike(like))
                    .order_by(Message.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return {
        "topics": [_topic_dict(t) for t in topics],
        "messages": [
            {
                "topic_id": m.topic_id,
                "message_id": m.id,
                "role": m.role.value if hasattr(m.role, "value") else str(m.role),
                "snippet": (m.content or "")[:280],
                "created_at": _iso(m.created_at),
            }
            for m in messages
        ],
    }


# --------------------------------------------------------------------------
# skills
# --------------------------------------------------------------------------
@mcp.tool()
async def list_skills() -> dict[str, Any]:
    """List Precursor skills (id, name, description) — reusable prompt presets."""
    if not await _section_enabled("skills"):
        return {"error": _GATED.format(section="skills")}
    async with SessionLocal() as session:
        rows = (await session.execute(select(Skill).order_by(Skill.name))).scalars().all()
    return {
        "skills": [{"id": s.id, "name": s.name, "description": s.description} for s in rows],
        "count": len(rows),
    }


@mcp.tool()
async def get_skill(skill_id: int) -> dict[str, Any]:
    """Get a skill including its full instructions text."""
    if not await _section_enabled("skills"):
        return {"error": _GATED.format(section="skills")}
    async with SessionLocal() as session:
        s = await session.get(Skill, skill_id)
    if s is None:
        return {"error": f"Skill {skill_id} not found"}
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "instructions": s.instructions,
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
async def _ensure_schedule_root(session: AsyncSession) -> Topic:
    root = (
        await session.execute(select(Topic).where(Topic.kind == "schedule_root"))
    ).scalar_one_or_none()
    if root is not None:
        return root
    root = Topic(
        title="Scheduled",
        slug=await allocate_unique_slug(session, "scheduled", Topic),
        kind="schedule_root",
    )
    session.add(root)
    await session.commit()
    await session.refresh(root)
    return root


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
    """Create a recurring scheduled topic that runs ``prompt`` on a cadence.

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
        root = await _ensure_schedule_root(session)
        topic = Topic(
            title=title,
            slug=await allocate_unique_slug(session, slugify(title) or "scheduled-topic", Topic),
            kind="scheduled",
            parent_id=root.id,
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
