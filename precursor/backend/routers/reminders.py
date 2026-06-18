"""Reminders router — one-shot date/time reminders, shared by topics and chats.

Endpoints are keyed by a ``{container}`` path segment ("topic" | "chat") plus
the container id, so a single router (and a single service) handles both kinds.
The sidebar's "Reminders" section reads ``GET /api/reminders`` (fired only).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models import Chat, Topic
from precursor.backend.schemas.reminder import (
    ContainerKind,
    ReminderCreate,
    ReminderItem,
    ReminderRead,
)
from precursor.backend.services import reminders as svc
from precursor.backend.services.reminder_ticker import get_reminder_ticker

router = APIRouter(prefix="/api/reminders", tags=["reminders"])

_CONTAINERS: set[str] = {"topic", "chat"}


def _validate_container(container: str) -> ContainerKind:
    if container not in _CONTAINERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown container")
    return container  # type: ignore[return-value]


@router.get("", response_model=list[ReminderItem])
async def list_reminders(
    session: AsyncSession = Depends(get_session),
) -> list[ReminderItem]:
    """List reminders awaiting acknowledgment (status='fired'), across containers."""
    items: list[ReminderItem] = []
    for reminder in await svc.list_fired(session):
        if reminder.topic_id is not None:
            topic = await session.get(Topic, reminder.topic_id)
            if topic is None:
                continue
            kind: ContainerKind = "topic"
            title, slug = topic.title, topic.slug
        elif reminder.chat_id is not None:
            chat = await session.get(Chat, reminder.chat_id)
            if chat is None:
                continue
            kind = "chat"
            title, slug = chat.title, chat.slug
        else:  # pragma: no cover - guarded by the container CHECK constraint
            continue
        items.append(
            ReminderItem.model_validate(
                {
                    **ReminderRead.model_validate(reminder).model_dump(),
                    "container": kind,
                    "title": title,
                    "slug": slug,
                }
            )
        )
    return items


@router.get("/{container}/{container_id}", response_model=ReminderRead)
async def get_reminder(
    container: str,
    container_id: int,
    session: AsyncSession = Depends(get_session),
) -> ReminderRead:
    kind = _validate_container(container)
    reminder = await svc.get_reminder(session, kind, container_id)
    if reminder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No reminder set")
    return ReminderRead.model_validate(reminder)


@router.put("/{container}/{container_id}", response_model=ReminderRead)
async def set_reminder(
    container: str,
    container_id: int,
    payload: ReminderCreate,
    session: AsyncSession = Depends(get_session),
) -> ReminderRead:
    """Create or replace the container's reminder (one per container)."""
    kind = _validate_container(container)
    if not await svc.container_exists(session, kind, container_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{kind.capitalize()} not found")
    reminder = await svc.set_reminder(
        session, kind, container_id, remind_at=payload.remind_at, note=payload.note
    )
    # A reminder set for now / the past should fire promptly, not next poll.
    await get_reminder_ticker().nudge()
    return ReminderRead.model_validate(reminder)


@router.delete("/{container}/{container_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_reminder(
    container: str,
    container_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Remove a pending or fired reminder (/reminder-cancel or /done)."""
    kind = _validate_container(container)
    if not await svc.delete_reminder(session, kind, container_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No reminder set")
