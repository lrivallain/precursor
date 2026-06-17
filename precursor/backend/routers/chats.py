"""Chat CRUD endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models import Chat, Message, MessageRole
from precursor.backend.schemas import ChatCreate, ChatRead, ChatUpdate
from precursor.backend.services.slugs import allocate_unique_slug, slugify

router = APIRouter(prefix="/api/chats", tags=["chats"])


@router.get("", response_model=list[ChatRead])
async def list_chats(
    q: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ChatRead]:
    """List non-archived chats, most recently updated first."""
    stmt = select(Chat).where(Chat.archived_at.is_(None)).order_by(Chat.updated_at.desc())
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(Chat.title.ilike(like))
    result = await session.execute(stmt)
    chats = list(result.scalars().all())

    # Compute unread counts.
    chat_ids = [c.id for c in chats]
    unread_by_id: dict[int, int] = {}
    if chat_ids:
        unread_result = await session.execute(
            select(Chat.id, func.count(Message.id).label("unread_count"))
            .join(Message, Message.chat_id == Chat.id)
            .where(Chat.last_read_at.is_not(None))
            .where(Message.role != MessageRole.USER)
            .where(Message.created_at > Chat.last_read_at)
            .where(Chat.id.in_(chat_ids))
            .group_by(Chat.id)
        )
        unread_by_id = {row[0]: row[1] for row in unread_result.all()}

    chat_reads: list[ChatRead] = []
    for chat in chats:
        data = {**chat.__dict__, "unread_count": unread_by_id.get(chat.id, 0)}
        chat_reads.append(ChatRead(**data))
    return chat_reads


@router.get("/archived", response_model=list[ChatRead])
async def list_archived_chats(
    session: AsyncSession = Depends(get_session),
) -> list[ChatRead]:
    """Flat list of archived chats, most recently archived first."""
    result = await session.execute(
        select(Chat).where(Chat.archived_at.is_not(None)).order_by(Chat.archived_at.desc())
    )
    chats = result.scalars().all()
    return [ChatRead.model_validate(c) for c in chats]


@router.post("", response_model=ChatRead, status_code=status.HTTP_201_CREATED)
async def create_chat(
    payload: ChatCreate,
    session: AsyncSession = Depends(get_session),
) -> Chat:
    """Create a new chat."""
    slug = await allocate_unique_slug(
        session,
        payload.slug or slugify(payload.title),
        Chat,
    )
    chat = Chat(
        title=payload.title,
        slug=slug,
        description=payload.description,
        pinned=payload.pinned,
    )
    session.add(chat)
    await session.commit()
    return chat


@router.get("/{chat_id}", response_model=ChatRead)
async def get_chat(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> Chat:
    """Get a specific chat."""
    chat = await session.get(Chat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@router.patch("/{chat_id}", response_model=ChatRead)
async def update_chat(
    chat_id: int,
    payload: ChatUpdate,
    session: AsyncSession = Depends(get_session),
) -> Chat:
    """Update a chat."""
    chat = await session.get(Chat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    if payload.title is not None:
        chat.title = payload.title
    if payload.description is not None:
        chat.description = payload.description
    if payload.pinned is not None:
        chat.pinned = payload.pinned
    if payload.slug is not None:
        slug = await allocate_unique_slug(session, payload.slug, Chat, exclude_id=chat.id)
        chat.slug = slug

    await session.commit()
    return chat


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Permanently delete a chat and its messages."""
    chat = await session.get(Chat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    await session.delete(chat)
    await session.commit()


@router.post("/{chat_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_chat_read(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Mark a chat as fully read."""
    chat = await session.get(Chat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    chat.last_read_at = datetime.now(UTC)
    await session.commit()


@router.post("/{chat_id}/archive", response_model=ChatRead)
async def archive_chat(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> Chat:
    """Archive a chat."""
    chat = await session.get(Chat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    chat.archived_at = datetime.now(UTC)
    await session.commit()
    return chat


@router.post("/{chat_id}/unarchive", response_model=ChatRead)
async def unarchive_chat(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> Chat:
    """Restore an archived chat."""
    chat = await session.get(Chat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    chat.archived_at = None
    await session.commit()
    return chat
