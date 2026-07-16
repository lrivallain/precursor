"""Global content-search endpoint backing the ⌘K palette."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.schemas.search import SearchResponse
from precursor.backend.services.search import search as run_search

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=SearchResponse)
async def search(
    q: str = Query("", description="Search text"),
    limit: int = Query(40, ge=1, le=40),
    session: AsyncSession = Depends(get_session),
) -> SearchResponse:
    return await run_search(session, q, limit)
