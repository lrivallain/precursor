"""Shared FastAPI dependencies for fetch-or-404 lookups.

These collapse the repeated ``entity = await session.get(Model, id); if entity
is None: raise 404`` guard into a single dependency. They are applied to
bodyless endpoints (GET/DELETE) so dependency resolution can't reorder a
request-body ``422`` ahead of the ``404`` the inline guard used to raise.
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.lookups import get_or_404
from precursor.backend.models import Chat, Topic


async def get_topic_or_404(
    topic_id: int,
    session: AsyncSession = Depends(get_session),
) -> Topic:
    topic = await get_or_404(session, Topic, topic_id, "Topic not found")
    return topic


async def get_chat_or_404(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> Chat:
    chat = await get_or_404(session, Chat, chat_id, "Chat not found")
    return chat
