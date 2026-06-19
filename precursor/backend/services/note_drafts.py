"""Helpers for note draft persistence and draft attachments."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import Attachment, NoteDraft, NoteDraftAttachment

NoteContainerKind = Literal["topic", "chat"]


def _container_kwargs(kind: NoteContainerKind, container_id: int) -> dict[str, int]:
    if kind == "topic":
        return {"topic_id": container_id}
    return {"chat_id": container_id}


async def get_note_draft(
    session: AsyncSession,
    *,
    kind: NoteContainerKind,
    container_id: int,
) -> NoteDraft | None:
    query = (
        select(NoteDraft).where(NoteDraft.topic_id == container_id)
        if kind == "topic"
        else select(NoteDraft).where(NoteDraft.chat_id == container_id)
    )
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def get_or_create_note_draft(
    session: AsyncSession,
    *,
    kind: NoteContainerKind,
    container_id: int,
) -> NoteDraft:
    draft = await get_note_draft(session, kind=kind, container_id=container_id)
    if draft is not None:
        return draft
    draft = NoteDraft(text="", **_container_kwargs(kind, container_id))
    session.add(draft)
    await session.flush()
    return draft


async def load_note_draft_attachments(
    session: AsyncSession,
    *,
    kind: NoteContainerKind,
    container_id: int,
    attachment_ids: list[int],
) -> list[NoteDraftAttachment]:
    if not attachment_ids:
        return []
    draft = await get_note_draft(session, kind=kind, container_id=container_id)
    if draft is None:
        return []
    result = await session.execute(
        select(NoteDraftAttachment).where(
            NoteDraftAttachment.note_draft_id == draft.id,
            NoteDraftAttachment.id.in_(attachment_ids),
        )
    )
    return list(result.scalars().all())


async def consume_note_draft_attachments_to_message(
    session: AsyncSession,
    *,
    kind: NoteContainerKind,
    container_id: int,
    message_id: int,
    attachment_ids: list[int],
) -> list[Attachment]:
    draft_attachments = await load_note_draft_attachments(
        session,
        kind=kind,
        container_id=container_id,
        attachment_ids=attachment_ids,
    )
    if not draft_attachments:
        return []

    bound: list[Attachment] = []
    for src in draft_attachments:
        att = Attachment(
            message_id=message_id,
            mime=src.mime,
            size=src.size,
            original_filename=src.original_filename,
            data=src.data,
            **_container_kwargs(kind, container_id),
        )
        session.add(att)
        bound.append(att)
        await session.delete(src)
    await session.flush()
    return bound
