"""Shared FastAPI dependencies for fetch-or-404 lookups.

These collapse the repeated ``entity = await session.get(Model, id); if entity
is None: raise 404`` guard into a single dependency. They are applied to
bodyless endpoints (GET/DELETE) so dependency resolution can't reorder a
request-body ``422`` ahead of the ``404`` the inline guard used to raise.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.lookups import get_or_404
from precursor.backend.models import Chat, Topic
from precursor.backend.services.remote_access.classify import is_local_caller


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


async def require_local(request: Request) -> None:
    """Mark a route local-only: only a caller on this machine gets an answer.

    Some features are not meant to be driven from a paired phone (controlling
    the tunnel itself...). A request through a tunnel, one addressed to another
    host (a DNS-rebinding page) or one from a foreign ``Origin`` gets 404, so
    the feature simply looks absent from there.
    """
    if not is_local_caller(request.scope):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
