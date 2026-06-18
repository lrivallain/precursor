"""Reminder service — container-agnostic logic shared by topics and chats.

A reminder targets exactly one container (a topic or a chat). Everything here is
keyed by a ``(container, container_id)`` pair so the router and the background
ticker share one implementation instead of duplicating topic/chat variants.

Lifecycle:

* ``set_reminder`` upserts the single reminder for a container (replacing any
  existing one) and schedules it.
* ``fire_due`` (called by the ticker) flips due reminders to ``"fired"``, posts
  a system message to the discussion so it goes unread + notifies, and returns
  the affected containers.
* ``complete_reminder`` / ``cancel_reminder`` delete the row (acknowledge /
  cancel).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import Chat, Message, MessageRole, Reminder, Topic
from precursor.backend.services.events import (
    publish_message_changed,
    publish_message_changed_chat,
    publish_reminder_changed,
)

logger = logging.getLogger(__name__)

ContainerKind = Literal["topic", "chat"]


def _now() -> datetime:
    return datetime.now(UTC)


def _filter(container: ContainerKind, container_id: int):  # type: ignore[no-untyped-def]
    col = Reminder.topic_id if container == "topic" else Reminder.chat_id
    return col == container_id


async def container_exists(
    session: AsyncSession, container: ContainerKind, container_id: int
) -> bool:
    model = Topic if container == "topic" else Chat
    return (await session.get(model, container_id)) is not None


async def get_reminder(
    session: AsyncSession, container: ContainerKind, container_id: int
) -> Reminder | None:
    result = await session.execute(select(Reminder).where(_filter(container, container_id)))
    return result.scalar_one_or_none()


async def set_reminder(
    session: AsyncSession,
    container: ContainerKind,
    container_id: int,
    *,
    remind_at: datetime,
    note: str | None,
) -> Reminder:
    """Create or replace the reminder for a container, then schedule it."""
    reminder = await get_reminder(session, container, container_id)
    if reminder is None:
        reminder = Reminder(
            topic_id=container_id if container == "topic" else None,
            chat_id=container_id if container == "chat" else None,
        )
        session.add(reminder)
    reminder.remind_at = remind_at
    reminder.note = note
    reminder.status = "scheduled"
    reminder.fired_at = None
    await session.commit()
    await session.refresh(reminder)
    await publish_reminder_changed(
        topic_id=reminder.topic_id, chat_id=reminder.chat_id
    )
    return reminder


async def delete_reminder(
    session: AsyncSession, container: ContainerKind, container_id: int
) -> bool:
    """Delete the container's reminder (acknowledge or cancel). Returns True if one existed."""
    reminder = await get_reminder(session, container, container_id)
    if reminder is None:
        return False
    topic_id, chat_id = reminder.topic_id, reminder.chat_id
    await session.delete(reminder)
    await session.commit()
    await publish_reminder_changed(topic_id=topic_id, chat_id=chat_id)
    return True


async def list_fired(session: AsyncSession) -> list[Reminder]:
    """All reminders awaiting acknowledgment, soonest-fired first."""
    result = await session.execute(
        select(Reminder).where(Reminder.status == "fired").order_by(Reminder.fired_at)
    )
    return list(result.scalars().all())


def _reminder_message(note: str | None) -> str:
    body = (note or "").strip()
    return f"⏰ Reminder: {body}" if body else "⏰ Reminder"


async def fire_due(session: AsyncSession) -> list[Reminder]:
    """Fire every reminder whose time has come.

    Posts a system message to each discussion (marking it unread + notifying),
    flips the reminder to ``"fired"``, and returns the reminders that fired so
    the caller can publish change events.
    """
    now = _now()
    result = await session.execute(
        select(Reminder).where(
            Reminder.status == "scheduled",
            Reminder.remind_at <= now,
        )
    )
    due = list(result.scalars().all())
    if not due:
        return []

    for reminder in due:
        message = Message(
            topic_id=reminder.topic_id,
            chat_id=reminder.chat_id,
            role=MessageRole.SYSTEM,
            content=_reminder_message(reminder.note),
        )
        session.add(message)
        reminder.status = "fired"
        reminder.fired_at = now
    await session.commit()

    for reminder in due:
        if reminder.topic_id is not None:
            await publish_message_changed(reminder.topic_id)
        elif reminder.chat_id is not None:
            await publish_message_changed_chat(reminder.chat_id)
        await publish_reminder_changed(
            topic_id=reminder.topic_id, chat_id=reminder.chat_id
        )
    return due
