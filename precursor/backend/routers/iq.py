"""Precursor IQ endpoints: ranked retrieval, cited answers, index status."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from precursor.backend.config import get_settings
from precursor.backend.schemas.iq import (
    IQAskRequest,
    IQAskResponse,
    IQHit,
    IQRetrieveResponse,
    IQSection,
    IQStatus,
)
from precursor.backend.services.iq import indexer
from precursor.backend.services.iq.ask import ask as run_ask
from precursor.backend.services.iq.retrieve import Hit
from precursor.backend.services.iq.retrieve import retrieve as run_retrieve

router = APIRouter(prefix="/api/iq", tags=["iq"])

# Entity kind (palette section) → container kind stored on each chunk.
_CONTAINER_OF: dict[str, str] = {
    "topics": "topic",
    "chats": "chat",
    "agents": "agent",
    "live": "live",
    "memory": "memory",
}


def _require_enabled() -> None:
    if not get_settings().iq_enabled:
        raise HTTPException(status_code=404, detail="Precursor IQ is disabled.")


def _containers(sections: list[str] | None) -> set[str] | None:
    if not sections:
        return None
    unknown = [s for s in sections if s not in _CONTAINER_OF]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown section(s): {', '.join(unknown)}")
    return {_CONTAINER_OF[s] for s in sections}


def _hit(hit: Hit) -> IQHit:
    return IQHit.model_validate(hit.as_dict())


@router.get("/retrieve", response_model=IQRetrieveResponse)
async def retrieve(
    q: str = Query("", description="Natural-language query"),
    limit: int = Query(10, ge=1, le=50),
    sections: str = Query("", description="Comma-separated entity kinds to include"),
) -> IQRetrieveResponse:
    _require_enabled()
    wanted = [s.strip() for s in sections.split(",") if s.strip()] or None
    # The palette queries per keystroke, so it gets a short catch-up budget.
    result = await run_retrieve(q, containers=_containers(wanted), limit=limit, drain_budget=0.3)
    return IQRetrieveResponse(
        query=result.query,
        hits=[_hit(h) for h in result.hits],
        markdown=result.markdown,
        lexical_backend=result.lexical_backend,
        semantic=result.semantic,
        pending=result.pending,
    )


@router.post("/ask", response_model=IQAskResponse)
async def ask(payload: IQAskRequest) -> IQAskResponse:
    _require_enabled()
    sections: list[IQSection] | None = payload.sections
    result = await run_ask(payload.question, containers=_containers(list(sections or [])))
    return IQAskResponse(
        question=result.question,
        answer=result.answer,
        model=result.model,
        citations=[_hit(h) for h in result.citations],
        sources=[_hit(h) for h in result.sources],
    )


@router.get("/status", response_model=IQStatus)
async def status() -> IQStatus:
    return IQStatus.model_validate(await indexer.status())


@router.post("/reindex", response_model=IQStatus)
async def reindex() -> IQStatus:
    """Queue every source again and index it in the background."""
    _require_enabled()
    await indexer.reindex()
    indexer.spawn_rebuild()
    return IQStatus.model_validate(await indexer.status())
