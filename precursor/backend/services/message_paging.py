"""Shared cursor-paginated windowing for a container's message transcript.

Topics and chats expose the same list-messages semantics: no params returns the
full transcript chronologically; ``limit`` returns the most recent ``limit``
rows; ``before_id`` pages further back. Either way the slice comes back oldest
first so the client can append it in render order.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, selectinload

from precursor.backend.models import Message

# Upper bound on a single windowed page, so a hostile/buggy client can't ask the
# server to materialise an unbounded slice at once.
MESSAGE_PAGE_MAX = 500


async def list_message_window(
    session: AsyncSession,
    fk_column: InstrumentedAttribute[int | None],
    container_id: int,
    *,
    limit: int | None,
    before_id: int | None,
) -> list[Message]:
    """Return a container's messages, optionally as a cursor-paginated window.

    ``fk_column`` is the ``Message`` foreign key to the container
    (``Message.topic_id`` or ``Message.chat_id``).
    """
    base = (
        select(Message)
        .where(fk_column == container_id)
        .options(selectinload(Message.attachments))
    )
    if limit is None and before_id is None:
        result = await session.execute(base.order_by(Message.created_at, Message.id))
        return list(result.scalars().all())
    if before_id is not None:
        base = base.where(Message.id < before_id)
    page = max(1, min(limit or MESSAGE_PAGE_MAX, MESSAGE_PAGE_MAX))
    result = await session.execute(base.order_by(Message.id.desc()).limit(page))
    rows = list(result.scalars().all())
    rows.reverse()
    return rows
