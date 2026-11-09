"""Fetch-by-primary-key helpers shared by routers and services.

``get_or_404`` replaces the guard that used to be repeated at every call site::

    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")

Use it whenever a missing row should end the request with a 404. For lookups
on another column (slug, public id, ...) keep the query explicit and raise the
same way.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession


async def get_or_404[T](
    session: AsyncSession,
    model: type[T],
    ident: Any,
    detail: str | None = None,
    **options: Any,
) -> T:
    """Return ``session.get(model, ident, **options)`` or raise a 404.

    ``detail`` defaults to ``"<Model> not found"``; ``options`` are passed to
    ``AsyncSession.get`` (``options=[selectinload(...)]``, ``with_for_update``...).
    """
    entity = await session.get(model, ident, **options)
    if entity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail or f"{model.__name__} not found")
    return entity
