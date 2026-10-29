"""OpenAI-compatible endpoint — lets OpenAI clients use Precursor's models.

Thin HTTP layer over :mod:`precursor.backend.services.openai_proxy`: every
failure is answered in OpenAI's ``{"error": {...}}`` shape (not FastAPI's
``detail``) because that's what the clients calling here know how to display.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.services.openai_proxy import (
    BASE_PATH,
    ProxyError,
    authorize,
    complete,
    list_proxy_models,
    open_completion,
    parse_completion_request,
    resolve_provider,
    stream_completion,
)

router = APIRouter(prefix=BASE_PATH, tags=["openai-compatible"])


def _error(exc: ProxyError) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
    return JSONResponse(status_code=exc.status, content=exc.body(), headers=headers)


@router.get("/models", response_model=None)
async def list_models(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    try:
        await authorize(session, authorization)
        provider = await resolve_provider(session)
        # Don't hold a pooled connection across the catalogue round-trip.
        await session.commit()
        return JSONResponse({"object": "list", "data": await list_proxy_models(provider)})
    except ProxyError as exc:
        return _error(exc)


@router.get("/models/{model_id:path}", response_model=None)
async def retrieve_model(
    model_id: str,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    try:
        await authorize(session, authorization)
        provider = await resolve_provider(session)
        await session.commit()
        for model in await list_proxy_models(provider):
            if model["id"] == model_id:
                return JSONResponse(model)
        raise ProxyError(
            404, f"The model {model_id!r} does not exist.", code="model_not_found", param="model"
        )
    except ProxyError as exc:
        return _error(exc)


@router.post("/chat/completions", response_model=None)
async def chat_completions(
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    try:
        await authorize(session, authorization)
        try:
            body = await request.json()
        except ValueError as exc:
            raise ProxyError(400, "The request body must be valid JSON.") from exc
        parsed = parse_completion_request(body)
        provider = await resolve_provider(session)
        # A reply can stream for minutes; the request session isn't needed again.
        await session.commit()
        events = await open_completion(provider, parsed)
        if parsed.stream:
            return StreamingResponse(
                stream_completion(events, parsed),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        return JSONResponse(await complete(events, parsed))
    except ProxyError as exc:
        return _error(exc)
