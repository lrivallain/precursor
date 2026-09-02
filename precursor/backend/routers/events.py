"""SSE endpoint that fans out cross-window events to connected browsers.

Also hosts the loopback republish endpoint that stdio MCP subprocesses use to
get their events onto *this* process's bus — see
:mod:`precursor.backend.services.events`.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from precursor.backend.services.events import (
    RELAY_TOKEN_ENV,
    RELAY_TOKEN_HEADER,
    RELAYABLE_EVENT_TYPES,
    Event,
    get_bus,
)

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


class EventPublish(BaseModel):
    """One relayed event. Only the ids a client needs to decide what to refetch.

    Fields the bus carries but a relay has no business setting (``client_id``,
    and the ``server``/``url`` payload of the MCP auth events) are absent, so
    they are dropped rather than honoured.
    """

    type: str = Field(min_length=1, max_length=64)
    topic_id: int | None = None
    chat_id: int | None = None
    agent_session_id: int | None = None
    meeting_session_id: int | None = None
    workflow_id: int | None = None
    status: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=200)


@router.post("/events/publish", status_code=status.HTTP_204_NO_CONTENT)
async def publish_relayed_event(payload: EventPublish, request: Request) -> None:
    """Republish an event raised in a child process onto this process's bus.

    Precursor's own MCP server (and the other in-tree ones) run as stdio
    subprocesses sharing the database but not the in-memory bus, so a write made
    there — filing a note, posting a message — reached no browser and looked
    like it had silently failed. The child forwards the event here instead.

    Precursor has no user auth, so two things bound this endpoint. The caller
    must present the per-process token, which exists only in the environment of
    processes this app spawned and is therefore unguessable from off-machine.
    And only the data-refresh event types are accepted: the payload names *what
    changed*, never what to do about it, so the most a relayed event can achieve
    is making a window refetch data it already has. Notably ``mcp.auth_url``,
    which steers a popup at a URL, is not relayable.

    A Host-header loopback check is deliberately *not* added on top: the child
    dials whatever address the app bound, so it would break a LAN bind while
    adding nothing the token doesn't already cover.
    """
    expected = _relay_token()
    presented = request.headers.get(RELAY_TOKEN_HEADER, "")
    # Constant-time so the token can't be recovered by timing the comparison.
    if not expected or not secrets.compare_digest(presented, expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid event relay token")
    if payload.type not in RELAYABLE_EVENT_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Event type {payload.type!r} is not relayable"
        )
    event: Event = {
        "type": payload.type,
        "topic_id": payload.topic_id,
        "chat_id": payload.chat_id,
        "agent_session_id": payload.agent_session_id,
        "meeting_session_id": payload.meeting_session_id,
        "workflow_id": payload.workflow_id,
        "status": payload.status,
        "name": payload.name,
        # Relayed writes originate from a tool call, not from a browser. Keeping
        # the client id empty makes them broadcast to *every* window, including
        # the one whose chat turn triggered the tool — which is exactly the
        # window that needs to re-render the new note.
        "client_id": None,
    }
    # dispatch(), not publish(): this is already a relayed payload, and
    # publish() would re-normalise (and, in a child, re-relay) it.
    get_bus().dispatch(event)


def _relay_token() -> str:
    return os.environ.get(RELAY_TOKEN_ENV, "").strip()
