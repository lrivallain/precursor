"""Shared plumbing for the Precursor MCP tools: the tool registrar, per-section
exposure gates, and the dict shapes tools return."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import quote

from mcp.server.mcpserver import MCPServer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    AgentSession,
    Chat,
    Collection,
    MeetingSession,
    Message,
    MessageRole,
    Reminder,
    Topic,
    TopicSchedule,
)
from precursor.backend.services.app_settings import (
    MCP_EXPOSE_SECTIONS,
    resolve_mcp_expose,
)
from precursor.backend.services.links import app_base_url
from precursor.backend.services.links import topic_paths as _topic_paths
from precursor.backend.services.mcp.server import TOOL_SECTIONS
from precursor.backend.services.message_links import message_path

if TYPE_CHECKING:
    pass


# Decorated tool functions are returned unchanged, so the registrar's decorator
# is identity-typed (preserves each tool's signature for callers/mypy).
F = TypeVar("F", bound=Callable[..., Any])


class _ToolRegistrar:
    """Collects ``@mcp.tool()``-decorated functions at import time.

    The module decorates tools against this registrar; :func:`build_mcp` then
    stamps them onto a *fresh* ``MCPServer`` instance. A fresh instance per app is
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


# Tools below decorate against the registrar; the stdio MCPServer instance is
# built at the end of the module (``_stdio_mcp``).
mcp = _registrar


class _GatedMCPServer(MCPServer):
    """An ``MCPServer`` whose ``tools/list`` only advertises exposed tools.

    Calls were already refused per section, but a client still saw (and a model
    still paid context for) every tool with nothing exposed. Listing the same
    gate keeps the advertised surface in step with Settings.
    """

    async def list_tools(self) -> list[Any]:
        tools = await super().list_tools()
        visible = await _exposed_sections()
        return [t for t in tools if _tool_visible(t.name, visible)]


# Cap list/search results so a huge instance can't blow the caller's context.
_MAX_ROWS = 200


_GATED = (
    "The '{section}' capability is not exposed by this Precursor instance. "
    "Enable it in Settings → MCP servers → Precursor capabilities."
)


# Sections that always honour their toggle, even for Precursor's own agents.
_STRICT_SECTIONS = frozenset({"workspaces", "workspaces_write"})


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


async def _section_enabled(section: str, *, strict: bool = False) -> bool:
    # First-party agent sessions launch this server with full access — the
    # ``mcp_expose`` toggles gate *external* MCP clients, not Precursor's own
    # agents, which must read topic content and post results back. ``strict``
    # sections (the filesystem ones) always honour their toggle.
    if not strict and os.environ.get("PRECURSOR_MCP_FULL_ACCESS") == "1":
        return True
    async with SessionLocal() as session:
        expose = await resolve_mcp_expose(session)
    return bool(expose.get(section))


async def _exposed_sections() -> set[str]:
    """Sections whose tools this caller may see right now (see ``_section_enabled``)."""
    async with SessionLocal() as session:
        expose = await resolve_mcp_expose(session)
    on = {s for s, enabled in expose.items() if enabled}
    if os.environ.get("PRECURSOR_MCP_FULL_ACCESS") == "1":
        on |= set(MCP_EXPOSE_SECTIONS) - _STRICT_SECTIONS
    return on


def _tool_visible(name: str, exposed: set[str]) -> bool:
    section = TOOL_SECTIONS.get(name)
    # A tool with no gating section (``precursor_info``) is always advertised.
    return section is None or section in exposed


def _topic_dict(
    t: Topic,
    collection_names: dict[int, str] | None = None,
    topic_paths: dict[int, str] | None = None,
) -> dict[str, Any]:
    names = collection_names or {}
    paths = topic_paths or {}
    return {
        "id": t.id,
        "slug": t.slug,
        "public_id": t.public_id,
        "title": t.title,
        "kind": t.kind,
        "description": t.description,
        "path": paths.get(t.id) or t.slug,
        "parent_id": t.parent_id,
        "collection_id": t.collection_id,
        "collection": names.get(t.collection_id) if t.collection_id is not None else None,
        "pinned": t.pinned,
        "archived": t.archived_at is not None,
        "created_at": _iso(t.created_at),
        "updated_at": _iso(t.updated_at),
    }


async def _collection_names(session: AsyncSession) -> dict[int, str]:
    rows = await session.execute(select(Collection.id, Collection.name))
    return {cid: name for cid, name in rows.all()}


async def _topic_ref(session: AsyncSession, topic: Topic) -> dict[str, Any]:
    """Human-readable identity for a topic a write tool just targeted.

    A bare numeric ``topic_id`` is meaningless to the person reading the answer —
    ids are an internal detail the UI never shows — so "I filed your note under
    topic 42" is unverifiable: right topic, or a neighbour? Echoing the title,
    the slug path and the in-app URL makes the destination checkable at a glance
    (and pasteable into the browser).
    """
    paths = await _topic_paths(session)
    path = paths.get(topic.id) or topic.slug
    return {
        "id": topic.id,
        "title": topic.title,
        # ``quote`` leaves "/" alone, so the joined segments stay a path.
        "url": f"{app_base_url()}/topics/{quote(path)}",
        "path": path,
    }


def _message_dict(m: Message, path: str) -> dict[str, Any]:
    return {
        "id": m.id,
        "public_id": m.public_id,
        "permalink": (
            message_path(path, m.public_id)
            if m.role in (MessageRole.USER, MessageRole.ASSISTANT)
            else None
        ),
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
        "public_id": c.public_id,
        "slug": c.slug,
        "title": c.title,
        "description": c.description,
        "pinned": c.pinned,
        "archived": c.archived_at is not None,
        "parent_topic_id": c.parent_topic_id,
        "parent_message_id": c.parent_message_id,
        "created_at": _iso(c.created_at),
        "updated_at": _iso(c.updated_at),
    }


def _agent_dict(a: AgentSession) -> dict[str, Any]:
    return {
        "id": a.id,
        "public_id": a.public_id,
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
        # Every rule the schedule fires on, primary first. Single-rule schedules
        # produce a one-element list matching the flat fields above.
        "rules": [rule.as_dict() for rule in s.recurrence_rules],
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
