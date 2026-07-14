"""Container-agnostic notes operations shared by the topic and chat routers.

The topic (``routers/commands.py``) and chat (``routers/chat_messages.py``)
notes endpoints are identical apart from which container (topic vs chat) they
target. This module holds that shared logic, keyed by ``kind``/``container_id``,
so each router endpoint is a thin guard-and-delegate wrapper.
"""

from __future__ import annotations

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import Message, MessageRole, NoteDraft, NoteDraftAttachment
from precursor.backend.schemas import (
    MessageRead,
    NoteDraftAttachmentRead,
    NotesAppendResponse,
    NotesDraftResponse,
)
from precursor.backend.services.blob_store import write_blob
from precursor.backend.services.events import (
    publish_message_changed,
    publish_message_changed_chat,
)
from precursor.backend.services.image_uploads import read_validated_attachment
from precursor.backend.services.note_drafts import (
    NoteContainerKind,
    consume_note_draft_attachments_to_message,
    container_kwargs,
    get_note_draft,
    get_or_create_note_draft,
)

# Shared LLM system prompt for the "clean up notes" action (topic + chat).
REPHRASE_SYSTEM = (
    "You clean up rough meeting / working notes. Preserve every fact and "
    "decision verbatim; do not invent content. Reorganise into short, "
    "scannable bullet points grouped by theme when useful, fix typos and "
    "obvious shorthand, and keep neutral phrasing. Output ONLY the cleaned "
    "notes in GitHub-Flavored Markdown — no preamble, no signature."
)


def build_rephrase_user_prompt(
    *, container_label: str, title: str, instruction: str, text: str
) -> str:
    """Build the user prompt for /notes rephrase (topic or chat)."""
    return (
        f"{container_label}: {title}\n\n"
        f"Extra instruction: {instruction or '(none — default cleanup)'}\n\n"
        f"Raw notes:\n{text}"
    )


async def _publish(kind: NoteContainerKind, container_id: int) -> None:
    if kind == "topic":
        await publish_message_changed(container_id)
    else:
        await publish_message_changed_chat(container_id)


def _draft_response(draft: NoteDraft) -> NotesDraftResponse:
    return NotesDraftResponse(
        text=draft.text,
        updated_at=draft.updated_at.isoformat() if draft.updated_at else None,
        attachments=[NoteDraftAttachmentRead.model_validate(a) for a in draft.attachments],
    )


async def append_notes(
    *,
    kind: NoteContainerKind,
    container_id: int,
    text: str,
    attachment_ids: list[int],
) -> NotesAppendResponse:
    """Persist freeform notes verbatim as a user message in the container."""
    trimmed = text.strip()
    if not trimmed and not attachment_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Notes text or attachments are required")

    body = "**Notes**" if not trimmed else f"**Notes**\n\n{trimmed}"
    async with SessionLocal() as write_session:
        msg = Message(
            role=MessageRole.USER,
            content=body,
            **container_kwargs(kind, container_id),
        )
        write_session.add(msg)
        await write_session.flush()
        await consume_note_draft_attachments_to_message(
            write_session,
            kind=kind,
            container_id=container_id,
            message_id=msg.id,
            attachment_ids=attachment_ids,
        )
        await write_session.commit()
        await write_session.refresh(msg, attribute_names=["attachments"])
        message_read = MessageRead.model_validate(msg, from_attributes=True)
    await _publish(kind, container_id)
    return NotesAppendResponse(message=message_read)


async def get_notes_draft(
    session: AsyncSession, *, kind: NoteContainerKind, container_id: int
) -> NotesDraftResponse:
    draft = await get_note_draft(session, kind=kind, container_id=container_id)
    if draft is None:
        return NotesDraftResponse(text=None, updated_at=None, attachments=[])
    await session.refresh(draft, attribute_names=["attachments"])
    return _draft_response(draft)


async def save_notes_draft(
    session: AsyncSession, *, kind: NoteContainerKind, container_id: int, text: str
) -> NotesDraftResponse:
    draft = await get_note_draft(session, kind=kind, container_id=container_id)
    if draft is None:
        draft = NoteDraft(text=text, **container_kwargs(kind, container_id))
        session.add(draft)
    else:
        draft.text = text
    await session.commit()
    await session.refresh(draft, attribute_names=["attachments"])
    return _draft_response(draft)


async def delete_notes_draft(
    session: AsyncSession, *, kind: NoteContainerKind, container_id: int
) -> None:
    draft = await get_note_draft(session, kind=kind, container_id=container_id)
    if draft is None:
        return
    await session.delete(draft)
    await session.commit()


async def list_notes_attachments(
    session: AsyncSession, *, kind: NoteContainerKind, container_id: int
) -> list[NoteDraftAttachment]:
    draft = await get_note_draft(session, kind=kind, container_id=container_id)
    if draft is None:
        return []
    await session.refresh(draft, attribute_names=["attachments"])
    return list(draft.attachments)


async def upload_notes_attachment(
    session: AsyncSession, *, kind: NoteContainerKind, container_id: int, file: UploadFile
) -> NoteDraftAttachment:
    mime, data = await read_validated_attachment(file)
    draft = await get_or_create_note_draft(session, kind=kind, container_id=container_id)
    att = NoteDraftAttachment(
        note_draft_id=draft.id,
        mime=mime,
        size=len(data),
        original_filename=(file.filename or "")[:255],
        sha256=write_blob(data),
    )
    session.add(att)
    await session.commit()
    await session.refresh(att)
    return att


async def delete_notes_attachment(
    session: AsyncSession, *, kind: NoteContainerKind, container_id: int, attachment_id: int
) -> None:
    draft = await get_note_draft(session, kind=kind, container_id=container_id)
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    att = await session.get(NoteDraftAttachment, attachment_id)
    if att is None or att.note_draft_id != draft.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    await session.delete(att)
    await session.commit()
