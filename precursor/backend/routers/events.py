"""SSE endpoint that fans out cross-window events to connected browsers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from precursor.backend.services.events import get_bus

router = APIRouter(prefix="/api", tags=["events"])

# Send a keepalive if no real event arrives within this many seconds, so
# corporate proxies don't kill an idle connection.
_HEARTBEAT_SECONDS = 15.0


@router.get("/events")
async def stream_events(request: Request) -> EventSourceResponse:
    bus = get_bus()

    async def gen() -> AsyncIterator[dict[str, str]]:
        async with bus.subscribe() as queue:
            yield {"event": "ready", "data": "{}"}
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield {
                    "event": event.get("type", "message"),
                    "data": json.dumps(event),
                }

    return EventSourceResponse(gen())
