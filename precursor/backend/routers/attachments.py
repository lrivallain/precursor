"""Attachments router — upload, serve, and (pre-commit) delete attachment blobs.

Attachment bytes are stored as content-addressed files on disk (see
``services/blob_store.py``); only metadata (mime, size, filename, SHA-256) lives
in the database, keeping the SQLite file small and cheap to back up.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models import Attachment, Chat, NoteDraftAttachment, Topic
from precursor.backend.schemas import AttachmentRead
from precursor.backend.services.blob_store import blob_path, write_blob
from precursor.backend.services.image_uploads import read_validated_attachment

logger = logging.getLogger(__name__)

router = APIRouter(tags=["attachments"])


@router.post(
    "/api/topics/{topic_id}/attachments",
    response_model=AttachmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    topic_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> Attachment:
    if await session.get(Topic, topic_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")

    mime, data = await read_validated_attachment(file)

    att = Attachment(
        topic_id=topic_id,
        message_id=None,
        mime=mime,
        size=len(data),
        original_filename=(file.filename or "")[:255],
        sha256=write_blob(data),
    )
    session.add(att)
    await session.commit()
    await session.refresh(att)
    return att


@router.post(
    "/api/chats/{chat_id}/attachments",
    response_model=AttachmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_chat_attachment(
    chat_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> Attachment:
    if await session.get(Chat, chat_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")

    mime, data = await read_validated_attachment(file)

    att = Attachment(
        chat_id=chat_id,
        message_id=None,
        mime=mime,
        size=len(data),
        original_filename=(file.filename or "")[:255],
        sha256=write_blob(data),
    )
    session.add(att)
    await session.commit()
    await session.refresh(att)
    return att


@router.get("/api/attachments/{attachment_id}")
async def get_attachment(
    attachment_id: int,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    att = await session.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    return _serve_blob(att.sha256, att.mime)


@router.get("/api/notes/attachments/{attachment_id}")
async def get_note_draft_attachment(
    attachment_id: int,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    att = await session.get(NoteDraftAttachment, attachment_id)
    if att is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    return _serve_blob(att.sha256, att.mime)


def _serve_blob(sha256: str, mime: str) -> FileResponse:
    path = blob_path(sha256)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment content missing")
    return FileResponse(
        path,
        media_type=mime,
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.delete("/api/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    attachment_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Drop an attachment that hasn't been bound to a message yet.

    Once an attachment is attached to a sent message, deletion must go through
    deleting the message itself (CASCADE). This avoids leaving messages whose
    rendered images suddenly 404.
    """
    att = await session.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    if att.message_id is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Attachment is already part of a sent message.",
        )
    await session.delete(att)
    await session.commit()
