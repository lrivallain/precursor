"""Chat CRUD endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models import Chat, Message, MessageRole, Topic
from precursor.backend.schemas import ChatCreate, ChatRead, ChatUpdate
from precursor.backend.schemas.topic import TopicRead
from precursor.backend.services.collections import (
    resolve_collection_default_role_id,
    resolve_collection_id,
)
from precursor.backend.services.events import publish_read_changed, publish_topic_changed
from precursor.backend.services.side_chats import to_chat_read, to_chat_reads
from precursor.backend.services.slugs import allocate_unique_slug, slugify
from precursor.backend.services.unread import message_unread_counts

router = APIRouter(prefix="/api/chats", tags=["chats"])


@router.get("", response_model=list[ChatRead])
async def list_chats(
    q: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ChatRead]:
    """List non-archived chats in a fixed creation order (newest first).

    Ordering by creation (not ``updated_at``) keeps the list stable: opening or
    replying to a chat never reshuffles it, so it stays easy to follow.
    """
    stmt = (
        select(Chat)
        .where(Chat.archived_at.is_(None))
        .order_by(Chat.created_at.desc(), Chat.id.desc())
    )
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(Chat.title.ilike(like))
    result = await session.execute(stmt)
    chats = list(result.scalars().all())

    # Compute unread counts.
    chat_ids = [c.id for c in chats]
    unread_by_id: dict[int, int] = {}
    if chat_ids:
        unread_by_id = await message_unread_counts(
            session, Chat, Message.chat_id, container_ids=chat_ids
        )

    return await to_chat_reads(session, chats, unread_by_id)


@router.get("/archived", response_model=list[ChatRead])
async def list_archived_chats(
    session: AsyncSession = Depends(get_session),
) -> list[ChatRead]:
    """Flat list of archived chats, most recently archived first."""
    result = await session.execute(
        select(Chat).where(Chat.archived_at.is_not(None)).order_by(Chat.archived_at.desc())
    )
    return await to_chat_reads(session, list(result.scalars().all()))


@router.post("", response_model=ChatRead, status_code=status.HTTP_201_CREATED)
async def create_chat(
    payload: ChatCreate,
    session: AsyncSession = Depends(get_session),
) -> ChatRead:
    """Create a new chat.

    Unlike topics, a chat's default slug is a random UUID rather than one derived
    from the title — so the common "New chat" default doesn't produce a wall of
    ``new-chat``, ``new-chat-2``… URLs. An explicit slug (or one set later in
    settings) still wins.
    """
    base = slugify(payload.slug) if payload.slug else uuid4().hex
    slug = await allocate_unique_slug(session, base or uuid4().hex, Chat)
    chat = Chat(
        title=payload.title,
        slug=slug,
        description=payload.description,
        description_as_system_prompt=payload.description_as_system_prompt,
        pinned=payload.pinned,
        role_id=payload.role_id,
        autoname_pending=payload.autoname,
    )
    session.add(chat)
    await session.commit()
    return await to_chat_read(session, chat)


@router.get("/by-slug/{slug}", response_model=ChatRead)
async def get_chat_by_slug(slug: str, session: AsyncSession = Depends(get_session)) -> ChatRead:
    """Resolve a chat by its slug (for /chats/<slug> deep links)."""
    result = await session.execute(select(Chat).where(Chat.slug == slug))
    chat = result.scalar_one_or_none()
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return await to_chat_read(session, chat)


@router.get("/{chat_id}", response_model=ChatRead)
async def get_chat(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> ChatRead:
    """Get a specific chat."""
    chat = await session.get(Chat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return await to_chat_read(session, chat)


@router.patch("/{chat_id}", response_model=ChatRead)
async def update_chat(
    chat_id: int,
    payload: ChatUpdate,
    session: AsyncSession = Depends(get_session),
) -> ChatRead:
    """Update a chat."""
    chat = await session.get(Chat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    if payload.title is not None:
        chat.title = payload.title
        # A title the user chose settles the name — a queued auto-name must not
        # come along afterwards and overwrite it.
        chat.autoname_pending = False
    if payload.description is not None:
        chat.description = payload.description
    if payload.description_as_system_prompt is not None:
        chat.description_as_system_prompt = payload.description_as_system_prompt
    if payload.pinned is not None:
        chat.pinned = payload.pinned
    if "role_id" in payload.model_fields_set:
        chat.role_id = payload.role_id
    if payload.slug is not None:
        slug = await allocate_unique_slug(session, payload.slug, Chat, exclude_id=chat.id)
        chat.slug = slug

    await session.commit()
    return await to_chat_read(session, chat)


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Permanently delete a chat and its messages."""
    chat = await session.get(Chat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    parent_topic_id = chat.parent_topic_id
    await session.delete(chat)
    await session.commit()
    if parent_topic_id is not None:
        await publish_topic_changed(parent_topic_id)


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
    # Let other tabs clear this chat's badge/counter in real time.
    await publish_read_changed(chat_id=chat_id)


@router.post("/{chat_id}/unread", status_code=status.HTTP_204_NO_CONTENT)
async def mark_chat_unread(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    chat = await session.get(Chat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    latest = await session.scalar(
        select(Message.created_at)
        .where(Message.chat_id == chat_id)
        .where(Message.role != MessageRole.USER)
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    chat.last_read_at = (latest or datetime.now(UTC)) - timedelta(microseconds=1)
    await session.commit()
    await publish_read_changed(chat_id=chat_id)


@router.post("/{chat_id}/archive", response_model=ChatRead)
async def archive_chat(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> ChatRead:
    """Archive a chat."""
    chat = await session.get(Chat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    chat.archived_at = datetime.now(UTC)
    await session.commit()
    if chat.parent_topic_id is not None:
        await publish_topic_changed(chat.parent_topic_id)
    return await to_chat_read(session, chat)


@router.post("/{chat_id}/unarchive", response_model=ChatRead)
async def unarchive_chat(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> ChatRead:
    """Restore an archived chat."""
    chat = await session.get(Chat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    chat.archived_at = None
    await session.commit()
    if chat.parent_topic_id is not None:
        await publish_topic_changed(chat.parent_topic_id)
    return await to_chat_read(session, chat)


@router.post("/{chat_id}/promote", response_model=TopicRead)
async def promote_chat_to_topic(
    chat_id: int,
    collection_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> Topic:
    """Promote a flat chat into a full topic.

    Creates a topic from the chat's title/description, re-parents every message
    onto the new topic (clearing chat_id), then deletes the now-empty chat. The
    transcript is preserved verbatim.

    Chats have no collection of their own, so the caller passes the one it is
    looking at; anything unresolvable falls back to the default. Leaving it null
    would strand the topic — no collection filter matches a null membership.

    A side chat becomes a sub-topic of the topic it was started from, in that
    topic's collection: a subtree never spans collections, so ``collection_id``
    is ignored then.
    """
    chat = await session.get(Chat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    parent = (
        await session.get(Topic, chat.parent_topic_id) if chat.parent_topic_id is not None else None
    )
    resolved_collection_id = (
        parent.collection_id
        if parent is not None and parent.collection_id is not None
        else await resolve_collection_id(session, collection_id)
    )
    topic = Topic(
        title=chat.title,
        slug=await allocate_unique_slug(session, slugify(chat.title) or "topic", Topic),
        description=chat.description,
        pinned=chat.pinned,
        parent_id=parent.id if parent is not None else None,
        collection_id=resolved_collection_id,
        role_id=await resolve_collection_default_role_id(session, resolved_collection_id),
    )
    session.add(topic)
    await session.flush()  # assign topic.id

    # Move the transcript over: exactly-one-container constraint stays satisfied
    # because we set topic_id and clear chat_id in the same statement.
    await session.execute(
        update(Message).where(Message.chat_id == chat_id).values(topic_id=topic.id, chat_id=None)
    )
    parent_topic_id = chat.parent_topic_id
    await session.delete(chat)
    await session.commit()
    await session.refresh(topic)
    await publish_topic_changed(topic.id)
    if parent_topic_id is not None:
        await publish_topic_changed(parent_topic_id)
    return topic
