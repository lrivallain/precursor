"""Streamable-HTTP client sessions on the MCP 2 transport.

MCP 2 removed ``streamablehttp_client(url, headers=, auth=)``: headers, auth and
timeouts now live on an ``httpx2.AsyncClient`` handed to
``streamable_http_client``. Both the pooled client and the WorkIQ sign-in path
open sessions this way, so the client construction — and the timeouts that go
with it — is defined once here.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import mcp.client.streamable_http as _sdk_streamable_http
from mcp import ClientSession, MCPError
from mcp.client.streamable_http import streamable_http_client

logger = logging.getLogger(__name__)

# The values MCP 1's ``streamablehttp_client`` defaulted to. A bare
# ``httpx2.AsyncClient`` falls back to a flat 5 s timeout, which would drop the
# long-lived GET stream a server holds open between events.
HTTP_TIMEOUT_SECONDS = 30.0
HTTP_SSE_READ_TIMEOUT_SECONDS = 300.0

# A whole tool result travels as one SSE event, and httpx2 caps an event at
# 1 MiB by default (MCP 1's parser had no cap). A week of WorkIQ calendar events
# with bodies already exceeds that; the SDK then swallows the ``SSEError`` and
# fails the call as "SSE stream ended without a response". Servers are
# configured by the user, so a generous-but-bounded cap is the right trade-off.
MCP_SSE_MAX_EVENT_BYTES = 16 * 1024 * 1024


def _event_source(response: httpx2.Response) -> httpx2.EventSource:
    return httpx2.EventSource(response, max_event_size=MCP_SSE_MAX_EVENT_BYTES)


def _raise_sse_event_cap() -> None:
    """Make the SDK's streamable-HTTP transport parse SSE with our event cap.

    ``streamable_http_client`` exposes no ``max_event_size`` knob, so rebind the
    two names its module resolves at call time: ``EventSource`` (POST responses,
    i.e. tool results) and ``sse_within_origin`` (the GET stream and resumption).
    """
    sdk = _sdk_streamable_http
    if getattr(sdk, "_precursor_sse_cap", False):
        return
    original_sse = getattr(sdk, "sse_within_origin", None)
    if not hasattr(sdk, "EventSource") or original_sse is None:
        logger.warning(
            "mcp.client.streamable_http no longer exposes EventSource/sse_within_origin; "
            "SSE events stay capped at httpx2's 1 MiB default."
        )
        return

    @asynccontextmanager
    async def sse_within_origin(*args: Any, **kwargs: Any) -> AsyncIterator[httpx2.EventSource]:
        async with original_sse(*args, **kwargs) as event_source:
            yield _event_source(event_source.response)

    sdk.EventSource = _event_source
    sdk.sse_within_origin = sse_within_origin  # type: ignore[attr-defined]
    sdk._precursor_sse_cap = True  # type: ignore[attr-defined]


_raise_sse_event_cap()


class MCPHTTPStatusError(MCPError):
    """An MCP request the endpoint answered with an HTTP error status.

    MCP 2 no longer raises for an HTTP error on a message POST: the request fails
    with an ``MCPError`` and the status is dropped. That error is either the
    server's own JSON-RPC error body (MCP 2's server answers a forgotten session
    with 404 ``Session not found``) or, without one, a generic stand-in
    (``Session terminated``, ``Server returned an error response``). Either way
    the status is what separates a 404/5xx (retry on a fresh session) from a 4xx
    (the request itself is wrong), so it is restored here.
    """

    def __init__(self, status: int, error: MCPError) -> None:
        super().__init__(error.code, f"{error.message} (HTTP {status})", error.data)
        self.status = status


class _StatusTrackingSession(ClientSession):
    """A ``ClientSession`` that re-attaches the HTTP status to failed requests.

    Every typed helper (``initialize``, ``list_tools``, ``call_tool``, …) routes
    through ``send_request``, so this is the one place to intercept.
    """

    def __init__(self, *args: Any, last_error_status: Callable[[], int | None], **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._last_error_status = last_error_status

    async def send_request(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return await super().send_request(*args, **kwargs)
        except MCPError as exc:
            status = self._last_error_status()
            # Exactly ``MCPError``: the SDK's subclasses carry their own meaning
            # (elicitation required, no back-channel) and must keep their type.
            if status is not None and type(exc) is MCPError:
                raise MCPHTTPStatusError(status, exc) from exc
            raise


def _post_status_recorder() -> tuple[
    Callable[[httpx2.Response], Awaitable[None]], Callable[[], int | None]
]:
    """An ``httpx2`` response hook remembering the latest failed POST's status.

    Only POSTs carry JSON-RPC requests; the long-lived GET stream is excluded.
    A later successful POST clears the value, so an OAuth 401 that the auth flow
    resolved is not mistaken for the status of a request that then failed, and a
    JSON-RPC error delivered on a 200 is never tagged. The pooled worker sends one
    request at a time, so "the latest POST" is the failing request's own.
    """
    last: list[int | None] = [None]

    async def hook(response: httpx2.Response) -> None:
        if response.request.method == "POST":
            last[0] = response.status_code if response.status_code >= 400 else None

    return hook, lambda: last[0]


@asynccontextmanager
async def streamable_http_session(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    auth: httpx2.Auth | None = None,
) -> AsyncIterator[ClientSession]:
    """Open an *uninitialized* ``ClientSession`` over streamable HTTP.

    ``auth`` must be an ``httpx2.Auth``: the SDK's OAuth providers subclass it,
    and an ``httpx.Auth`` would be rejected by the ``httpx2`` client.
    """
    timeout = httpx2.Timeout(HTTP_TIMEOUT_SECONDS, read=HTTP_SSE_READ_TIMEOUT_SECONDS)
    hook, last_error_status = _post_status_recorder()
    async with (
        httpx2.AsyncClient(
            headers=headers, auth=auth, timeout=timeout, event_hooks={"response": [hook]}
        ) as http_client,
        streamable_http_client(url, http_client=http_client) as (read, write),
        _StatusTrackingSession(read, write, last_error_status=last_error_status) as session,
    ):
        yield session
