"""Shared unread-count query for message containers (topics and chats).

Both the topic tree and the chat list compute the same "unread badge": count
non-user messages created after the container's ``last_read_at``. A container
with ``last_read_at`` unset is treated as fully read, so background history
never surfaces retroactively.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from precursor.backend.models import Chat, Message, MessageRole, Topic


async def message_unread_counts(
    session: AsyncSession,
    container_model: type[Topic] | type[Chat],
    fk_column: InstrumentedAttribute[int | None],
    *,
    container_ids: list[int] | None = None,
) -> dict[int, int]:
    """Map container id -> count of unread (non-user) messages.

    ``fk_column`` is the ``Message`` foreign key to the container
    (``Message.topic_id`` or ``Message.chat_id``). Pass ``container_ids`` to
    restrict the count to a specific set of containers; omit it to count across
    all of them.
    """
    stmt = (
        select(container_model.id, func.count(Message.id))
        .join(Message, fk_column == container_model.id)
        .where(container_model.last_read_at.is_not(None))
        .where(Message.role != MessageRole.USER)
        .where(Message.created_at > container_model.last_read_at)
        .group_by(container_model.id)
    )
    if container_ids is not None:
        stmt = stmt.where(container_model.id.in_(container_ids))
    result = await session.execute(stmt)
    return {row[0]: row[1] for row in result.all()}
