"""In-process pub/sub for live UI sync.

The bus exists so that multiple browser windows (or future native clients)
viewing the same Precursor instance can react to mutations originating
elsewhere. Events are tiny: a type plus an optional ``topic_id`` so a
listener can decide whether the change affects what it's currently showing.

A contextvar carries the originating client's id (set by middleware from the
``X-Client-Id`` request header). The SSE endpoint forwards it to every
subscriber, and each window filters out its own echoes client-side.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TypedDict


class Event(TypedDict, total=False):
    type: str
    topic_id: int | None
    chat_id: int | None
    agent_session_id: int | None
    # Carried only by ``mcp.auth_required`` — which MCP server needs an
    # interactive sign-in and the human-readable reason to show.
    server: str | None
    message: str | None
    client_id: str | None


_current_client_id: ContextVar[str | None] = ContextVar("precursor_client_id", default=None)


def set_current_client_id(value: str | None) -> None:
    _current_client_id.set(value)


def get_current_client_id() -> str | None:
    return _current_client_id.get()


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[Event]]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.add(q)
        try:
            yield q
        finally:
            async with self._lock:
                self._subscribers.discard(q)

    async def publish(self, event: Event) -> None:
        payload: Event = {
            "type": event["type"],
            "topic_id": event.get("topic_id"),
            "chat_id": event.get("chat_id"),
            "agent_session_id": event.get("agent_session_id"),
            "server": event.get("server"),
            "message": event.get("message"),
            "client_id": event.get("client_id") or _current_client_id.get(),
        }
        # Snapshot to avoid mutation during iteration.
        for q in list(self._subscribers):
            # Slow consumer — drop this event for that subscriber rather than
            # blocking publishers.
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(payload)


_bus = EventBus()


def get_bus() -> EventBus:
    return _bus


async def publish_topic_changed(topic_id: int | None = None) -> None:
    await _bus.publish({"type": "topic.changed", "topic_id": topic_id})


async def publish_message_changed(topic_id: int) -> None:
    await _bus.publish({"type": "message.changed", "topic_id": topic_id})


async def publish_mcp_auth_required(
    server: str, message: str, *, topic_id: int | None = None
) -> None:
    """Signal that an MCP server needs an interactive sign-in to proceed.

    Background work (a scheduled ``/guard`` probe, a chat turn) can't pop a
    browser, so it surfaces ``needs_auth`` and emits this so the app-global
    ``McpAuthBanner`` offers an inline re-authenticate action — the same UX a
    live turn gets, but reaching windows that weren't streaming the run.
    """
    await _bus.publish(
        {
            "type": "mcp.auth_required",
            "server": server,
            "message": message,
            "topic_id": topic_id,
        }
    )


async def publish_message_changed_chat(chat_id: int) -> None:
    await _bus.publish({"type": "message.changed", "chat_id": chat_id})


async def publish_stream_started(topic_id: int) -> None:
    await _bus.publish({"type": "stream.started", "topic_id": topic_id})


async def publish_stream_started_chat(chat_id: int) -> None:
    await _bus.publish({"type": "stream.started", "chat_id": chat_id})


async def publish_stream_ended(topic_id: int) -> None:
    await _bus.publish({"type": "stream.ended", "topic_id": topic_id})


async def publish_stream_ended_chat(chat_id: int) -> None:
    await _bus.publish({"type": "stream.ended", "chat_id": chat_id})


async def publish_reminder_changed(
    *, topic_id: int | None = None, chat_id: int | None = None
) -> None:
    """Signal that the set of reminders changed (created / fired / cleared).

    Carries the affected container id so a window viewing it can react; the
    sidebar Reminders section reloads regardless.
    """
    await _bus.publish({"type": "reminder.changed", "topic_id": topic_id, "chat_id": chat_id})


async def publish_agent_changed(
    *,
    agent_session_id: int | None = None,
    topic_id: int | None = None,
    chat_id: int | None = None,
) -> None:
    """Signal that an agent session's state or event stream changed.

    Carries the agent session id (so the Agents tab / an open session view can
    react) plus the linked container, if any, so a window viewing that topic or
    chat can refresh its agent badge.
    """
    await _bus.publish(
        {
            "type": "agent.changed",
            "agent_session_id": agent_session_id,
            "topic_id": topic_id,
            "chat_id": chat_id,
        }
    )


async def publish_read_changed(
    *,
    topic_id: int | None = None,
    chat_id: int | None = None,
    agent_session_id: int | None = None,
) -> None:
    """Signal that a conversation was marked read (its ``last_read_at`` advanced).

    Emitted by the ``/read`` endpoints so *other* tabs clear the unread badge and
    counter for the same discussion in real time. It carries only the affected
    id; receivers re-fetch that section's unread state. Deliberately distinct
    from ``message.changed`` and never triggers a re-mark, so it can't loop with
    the "mark the actively-viewed conversation read" logic. Echo-filtered for the
    originating tab, which already updated optimistically.
    """
    await _bus.publish(
        {
            "type": "read.changed",
            "topic_id": topic_id,
            "chat_id": chat_id,
            "agent_session_id": agent_session_id,
        }
    )
