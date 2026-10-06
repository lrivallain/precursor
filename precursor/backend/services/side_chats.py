"""Side chats — chats started from a topic, or from one of its replies.

A side chat holds a tangent away from the topic's transcript while keeping the
topic as grounding: its system prompt carries the parent topic's title,
description and summary, plus a frozen copy of the reply it was started from.

SQLite doesn't enforce the ``ON DELETE SET NULL`` on the parent links, so every
path that deletes topics or topic messages calls into the ``detach_*`` helpers
here first. Leaving a link dangling would let a recycled message id point a
chat's "jump to source" at an unrelated reply.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import Select, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import (
    Chat,
    Message,
    MessageRole,
    NoteDraft,
    Reminder,
    Topic,
    TopicSummary,
)
from precursor.backend.schemas.chat import ChatRead, SideChatItem, SideChatReminder
from precursor.backend.services.slugs import allocate_unique_slug
from precursor.backend.services.unread import message_unread_counts

# The copied reply rides along in every turn's system prompt: keep it bounded.
SEED_MAX_CHARS = 12_000


def _truncate_seed(text: str) -> str:
    text = text.strip()
    if len(text) <= SEED_MAX_CHARS:
        return text
    return text[:SEED_MAX_CHARS].rstrip() + "\n\n…(truncated)"


# A placeholder: autonaming replaces it from the first message. The topic isn't
# repeated in it — every place a side chat is listed already shows its topic.
PLACEHOLDER_TITLE = "Side chat"


async def create_side_chat(
    session: AsyncSession,
    topic_id: int,
    *,
    message_id: int | None = None,
    quote: str | None = None,
) -> Chat:
    """Create a chat linked to ``topic_id`` (and to one of its replies).

    ``quote`` narrows the copied reply to an excerpt of it (a text selection).
    """
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    excerpt = (quote or "").strip()
    if excerpt and message_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "A quote needs the reply it comes from."
        )

    seed: str | None = None
    if message_id is not None:
        msg = await session.get(Message, message_id)
        if msg is None or msg.topic_id != topic_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
        if msg.role != MessageRole.ASSISTANT or msg.kind is not None or not msg.content.strip():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "A side chat can only start from an assistant reply.",
            )
        seed = _truncate_seed(excerpt or msg.content)

    # A random slug, like any other new chat (see routers/chats.py).
    chat = Chat(
        title=PLACEHOLDER_TITLE,
        slug=await allocate_unique_slug(session, uuid4().hex, Chat),
        role_id=topic.role_id,
        autoname_pending=True,
        parent_topic_id=topic.id,
        parent_message_id=message_id,
        seed_content=seed,
    )
    session.add(chat)
    await session.commit()
    return chat


async def build_parent_topic_context(session: AsyncSession, chat: Chat) -> str:
    """System-prompt block grounding a side chat in its parent topic.

    Empty for a plain chat. A detached chat (its topic was deleted) keeps the
    copied reply, which is all it still has.
    """
    parts: list[str] = []
    topic = await session.get(Topic, chat.parent_topic_id) if chat.parent_topic_id else None
    if topic is not None:
        parts.append(
            "This is a side chat started from a topic. Use the topic below as "
            "background; the user wants to explore a tangent here without "
            "touching the topic's own conversation."
        )
        parts.append(f"Parent topic title: {topic.title}")
        if topic.description:
            parts.append(f"Parent topic description: {topic.description}")
        summary = (
            await session.execute(
                select(TopicSummary.content).where(TopicSummary.topic_id == topic.id)
            )
        ).scalar_one_or_none()
        if summary and summary.strip():
            parts.append(f"Parent topic summary:\n{summary.strip()}")
    if chat.seed_content:
        parts.append(seed_prompt(chat.seed_content, "chat", in_parent_topic=topic is not None))
    return "\n\n".join(parts)


def seed_prompt(seed: str, container: str, *, in_parent_topic: bool = False) -> str:
    """Frame the reply a conversation started from for the system prompt."""
    where = " in that topic" if in_parent_topic else ""
    return (
        f"The user started this {container} from the following assistant reply{where}. "
        "Treat it as the subject of the discussion:\n"
        f"<<<REPLY\n{seed}\nREPLY>>>"
    )


async def parent_topic_titles(session: AsyncSession, chats: list[Chat]) -> dict[int, str]:
    ids = {c.parent_topic_id for c in chats if c.parent_topic_id is not None}
    if not ids:
        return {}
    rows = await session.execute(select(Topic.id, Topic.title).where(Topic.id.in_(ids)))
    return {tid: title for tid, title in rows.all()}


async def to_chat_reads(
    session: AsyncSession, chats: list[Chat], unread: dict[int, int] | None = None
) -> list[ChatRead]:
    """Serialize chats with their parent topic title resolved."""
    titles = await parent_topic_titles(session, chats)
    out: list[ChatRead] = []
    for chat in chats:
        read = ChatRead.model_validate(chat)
        read.unread_count = (unread or {}).get(chat.id, 0)
        if chat.parent_topic_id is not None:
            read.parent_topic_title = titles.get(chat.parent_topic_id)
        out.append(read)
    return out


async def to_chat_read(session: AsyncSession, chat: Chat) -> ChatRead:
    return (await to_chat_reads(session, [chat]))[0]


async def list_side_chats(session: AsyncSession, topic_id: int) -> list[SideChatItem]:
    """Non-archived side chats of a topic, newest first, with activity stats."""
    if await session.get(Topic, topic_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    chats = list(
        (
            await session.execute(
                select(Chat)
                .where(Chat.parent_topic_id == topic_id, Chat.archived_at.is_(None))
                .order_by(Chat.created_at.desc(), Chat.id.desc())
            )
        )
        .scalars()
        .all()
    )
    if not chats:
        return []
    ids = [c.id for c in chats]
    unread = await message_unread_counts(session, Chat, Message.chat_id, container_ids=ids)
    stats: dict[int, tuple[int, datetime | None]] = {
        cid: (count, last)
        for cid, count, last in (
            await session.execute(
                select(Message.chat_id, func.count(Message.id), func.max(Message.created_at))
                .where(Message.chat_id.in_(ids))
                .group_by(Message.chat_id)
            )
        ).all()
        if cid is not None
    }
    reminders = {
        r.chat_id: r
        for r in (await session.execute(select(Reminder).where(Reminder.chat_id.in_(ids))))
        .scalars()
        .all()
    }
    items: list[SideChatItem] = []
    for chat in chats:
        count, last = stats.get(chat.id, (0, None))
        reminder = reminders.get(chat.id)
        items.append(
            SideChatItem(
                id=chat.id,
                slug=chat.slug,
                title=chat.title,
                parent_message_id=chat.parent_message_id,
                from_reply=chat.seed_content is not None,
                unread_count=unread.get(chat.id, 0),
                message_count=count,
                last_message_at=last,
                created_at=chat.created_at,
                reminder=(
                    SideChatReminder(remind_at=reminder.remind_at, status=reminder.status)
                    if reminder is not None
                    else None
                ),
            )
        )
    return items


async def detach_topic(session: AsyncSession, topic_id: int) -> list[int]:
    """Unlink every side chat of a topic about to be deleted. Doesn't commit.

    Returns the affected chat ids so the caller can notify their windows.
    """
    ids = list(
        (await session.execute(select(Chat.id).where(Chat.parent_topic_id == topic_id)))
        .scalars()
        .all()
    )
    if ids:
        await session.execute(
            update(Chat)
            .where(Chat.id.in_(ids))
            .values(parent_topic_id=None, parent_message_id=None)
        )
    return ids


async def detach_messages(session: AsyncSession, message_ids: Select[Any]) -> None:
    """Forget the source reply of side chats whose source is about to be deleted.

    ``message_ids`` selects the ids being deleted. The copied reply stays: the
    chat only loses its "jump to source" link. Doesn't commit.
    """
    await session.execute(
        update(Chat).where(Chat.parent_message_id.in_(message_ids)).values(parent_message_id=None)
    )


async def discard_if_untouched(session: AsyncSession, chat_id: int) -> int | None:
    """Delete a side chat the user opened and left without using it.

    Untouched means: still on its placeholder title, no messages, nothing the
    user set on it (reminder, notes draft, description, pin). Anything else is
    kept. Returns the parent topic id when the chat was deleted (``None`` for
    a detached one, which is deleted all the same), or raises 404/409.
    """
    chat = await session.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    used = (
        not chat.autoname_pending
        or chat.pinned
        or bool((chat.description or "").strip())
        or (chat.parent_topic_id is None and chat.seed_content is None)
        or await session.scalar(select(exists().where(Message.chat_id == chat_id)))
        or await session.scalar(select(exists().where(Reminder.chat_id == chat_id)))
        or await session.scalar(select(exists().where(NoteDraft.chat_id == chat_id)))
    )
    if used:
        raise HTTPException(status.HTTP_409_CONFLICT, "This chat is in use; it was kept.")
    parent_topic_id = chat.parent_topic_id
    await session.delete(chat)
    await session.commit()
    return parent_topic_id


async def archive_with_topic(session: AsyncSession, topic_id: int, at: datetime) -> list[int]:
    """Archive a topic's live side chats with it, stamped with the topic's time.

    Sharing the timestamp is what lets :func:`unarchive_with_topic` restore
    exactly these, and not a side chat archived on its own. Doesn't commit.
    """
    ids = list(
        (
            await session.execute(
                select(Chat.id).where(Chat.parent_topic_id == topic_id, Chat.archived_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    if ids:
        await session.execute(update(Chat).where(Chat.id.in_(ids)).values(archived_at=at))
    return ids


async def unarchive_with_topic(
    session: AsyncSession, topic_id: int, archived_at: datetime
) -> list[int]:
    """Restore the side chats archived along with the topic. Doesn't commit."""
    ids = list(
        (
            await session.execute(
                select(Chat.id).where(
                    Chat.parent_topic_id == topic_id, Chat.archived_at == archived_at
                )
            )
        )
        .scalars()
        .all()
    )
    if ids:
        await session.execute(update(Chat).where(Chat.id.in_(ids)).values(archived_at=None))
    return ids
