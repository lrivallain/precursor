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
