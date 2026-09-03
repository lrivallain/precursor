"""In-process pub/sub for live UI sync.

The bus exists so that multiple browser windows (or future native clients)
viewing the same Precursor instance can react to mutations originating
elsewhere. Events are tiny: a type plus an optional ``topic_id`` so a
listener can decide whether the change affects what it's currently showing.

A contextvar carries the originating client's id (set by middleware from the
``X-Client-Id`` request header). The SSE endpoint forwards it to every
subscriber, and each window filters out its own echoes client-side.

The bus is *per process*, which matters because several built-in MCP servers —
including ``precursor`` itself — run as stdio subprocesses that share the app's
database but not its memory. A write performed there (``append_note``,
``post_message``, a reminder) landed in the DB while its ``message.changed``
event was published onto the subprocess's own empty bus, so no browser ever
learned about it and the note stayed invisible until a manual reload. Child
processes therefore *relay*: :func:`relay_child_env` builds the environment the
parent hands the child, pointing it at the app's loopback
``POST /api/events/publish``, which republishes onto the real bus. The app
process itself calls :func:`mark_app_process` and never relays, so there is no
loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TypedDict

logger = logging.getLogger(__name__)

#: Env vars the app process sets on every stdio MCP child so it can relay.
RELAY_URL_ENV = "PRECURSOR_EVENT_RELAY_URL"
RELAY_TOKEN_ENV = "PRECURSOR_EVENT_RELAY_TOKEN"

#: Header carrying the shared secret on a relayed publish.
RELAY_TOKEN_HEADER = "X-Precursor-Event-Token"

#: Event types a child process may ask the app to republish.
#:
#: Deliberately an allowlist of *data-refresh* signals. Anything on the bus that
#: makes a window act on attacker-controllable content — ``mcp.auth_url`` steers
#: a popup at a URL — stays out, so the endpoint can only ever cause a re-fetch.
RELAYABLE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "topic.changed",
        "chat.changed",
        "message.changed",
        "reminder.changed",
        "read.changed",
        "agent.changed",
        "meeting.changed",
        "workflow.changed",
    }
)

# Relay timeout. Generous enough for a loopback round-trip, short enough that a
# stopped parent can't stall the tool call that triggered the event.
_RELAY_TIMEOUT_SECONDS = 3.0


class Event(TypedDict, total=False):
    type: str
    topic_id: int | None
    chat_id: int | None
    agent_session_id: int | None
    # Which *execution* of that agent changed. Agents are shared definitions, so
    # a client watching one workflow's step needs the run to tell its own
    # progress apart from a concurrent driver's.
    agent_run_id: int | None
    meeting_session_id: int | None
    # Carried by ``mcp.auth_required`` (which MCP server needs an interactive
    # sign-in and the human-readable reason to show) and ``mcp.auth_resolved``
    # (which server's sign-in was just renewed).
    server: str | None
    message: str | None
    # Carried only by ``mcp.auth_url`` — the interactive OAuth authorization URL
    # the window that started the sign-in should open in a script-opened popup.
    url: str | None
    # Carried by ``mcp.server_state`` — the connection state the background
    # warm-up resolved a server to, and how many tools it now advertises.
    state: str | None
    tools: int | None
    # Carried by ``workflow.changed``: which pipeline changed, and the run state
    # + name the client needs to raise a notification without re-fetching.
    workflow_id: int | None
    status: str | None
    name: str | None
    client_id: str | None


_current_client_id: ContextVar[str | None] = ContextVar("precursor_client_id", default=None)


def set_current_client_id(value: str | None) -> None:
    _current_client_id.set(value)


def get_current_client_id() -> str | None:
    return _current_client_id.get()


# Whether this interpreter is the app (the process that serves /api/events).
# Only a *child* relays, so marking the app is what prevents a publish loop.
_is_app_process = False


def mark_app_process() -> None:
    """Declare this process the SSE origin, disabling outbound relaying."""
    global _is_app_process
    _is_app_process = True


def is_app_process() -> bool:
    return _is_app_process


def relay_child_env(*, host: str, port: int, token: str) -> dict[str, str]:
    """Env additions that let a stdio child relay its events back to the app.

    ``host`` is the app's *bind*, which may be a wildcard (``0.0.0.0``/``::``)
    that is not a connectable address — dial loopback in that case, since the
    child is by definition on this machine.
    """
    dial = {"0.0.0.0": "127.0.0.1", "": "127.0.0.1", "::": "::1"}.get(host, host)
    authority = f"[{dial}]:{port}" if ":" in dial else f"{dial}:{port}"
    return {
        RELAY_URL_ENV: f"http://{authority}/api/events/publish",
        RELAY_TOKEN_ENV: token,
    }


def new_relay_token() -> str:
    return secrets.token_urlsafe(32)


def _relay_target() -> tuple[str, str] | None:
    """The (url, token) this child should relay to, or None when it shouldn't."""
    if _is_app_process:
        return None
    url = os.environ.get(RELAY_URL_ENV, "").strip()
    token = os.environ.get(RELAY_TOKEN_ENV, "").strip()
    if not url or not token:
        return None
    return url, token


async def _relay(event: Event) -> None:
    """Best-effort forward of one event to the app process.

    Failure is never fatal: the write it announces is already committed, and an
    MCP tool must not error because the UI could not be nudged.
    """
    target = _relay_target()
    if target is None or event.get("type") not in RELAYABLE_EVENT_TYPES:
        return
    url, token = target
    try:
        import httpx

        async with httpx.AsyncClient(timeout=_RELAY_TIMEOUT_SECONDS) as client:
            await client.post(url, json=dict(event), headers={RELAY_TOKEN_HEADER: token})
    except Exception as exc:  # advisory channel — never fail the write it announces
        logger.debug("event relay to %s failed: %s", url, exc)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[Event]]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.add(q)
        try:
            yield q
        finally:
            async with self._lock:
                self._subscribers.discard(q)

    async def publish(self, event: Event) -> None:
        payload: Event = {
            "type": event["type"],
            "topic_id": event.get("topic_id"),
            "chat_id": event.get("chat_id"),
            "agent_session_id": event.get("agent_session_id"),
            "meeting_session_id": event.get("meeting_session_id"),
            "server": event.get("server"),
            "message": event.get("message"),
            "url": event.get("url"),
            "workflow_id": event.get("workflow_id"),
            "status": event.get("status"),
            "name": event.get("name"),
            "client_id": event.get("client_id") or _current_client_id.get(),
        }
        self.dispatch(payload)
        # In a stdio MCP child there are no local subscribers; hand the event to
        # the app process so the browser actually hears about the write.
        await _relay(payload)

    def dispatch(self, payload: Event) -> None:
        """Fan one already-normalised event out to local subscribers only."""
        # Snapshot to avoid mutation during iteration.
        for q in list(self._subscribers):
            # Slow consumer — drop this event for that subscriber rather than
            # blocking publishers.
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(payload)


_bus = EventBus()


def get_bus() -> EventBus:
    return _bus


async def publish_topic_changed(topic_id: int | None = None) -> None:
    await _bus.publish({"type": "topic.changed", "topic_id": topic_id})


async def publish_message_changed(topic_id: int) -> None:
    await _bus.publish({"type": "message.changed", "topic_id": topic_id})


async def publish_chat_changed(chat_id: int | None = None, *, broadcast: bool = False) -> None:
    """Signal that a chat's own metadata changed (title, most notably).

    The chat counterpart to ``topic.changed``: ``message.changed`` already covers
    the transcript, but a rename touches no message and would otherwise stay
    invisible in other windows until a reload.

    ``broadcast`` opts out of the usual echo filtering. Publishes are normally
    tagged with the originating client id so the window that made the change
    ignores its own event and keeps its optimistic update. Auto-naming inverts
    that: it runs in a detached task that inherited the request's client id, yet
    the window that started the chat made no optimistic update and is precisely
    the one that must hear about the new title.
    """
    if broadcast:
        token = _current_client_id.set(None)
        try:
            await _bus.publish({"type": "chat.changed", "chat_id": chat_id})
        finally:
            _current_client_id.reset(token)
        return
    await _bus.publish({"type": "chat.changed", "chat_id": chat_id})


async def publish_mcp_auth_required(
    server: str, message: str, *, topic_id: int | None = None
) -> None:
    """Signal that an MCP server needs an interactive sign-in to proceed.

    Background work (a scheduled ``/guard`` probe, a chat turn) can't pop a
    browser, so it surfaces ``needs_auth`` and emits this so the app-global
    ``McpAuthBanner`` offers an inline re-authenticate action — the same UX a
    live turn gets, but reaching windows that weren't streaming the run.
    """
    await _bus.publish(
        {
            "type": "mcp.auth_required",
            "server": server,
            "message": message,
            "topic_id": topic_id,
        }
    )


async def publish_mcp_server_state(server: str, state: str, *, tools: int) -> None:
    """Announce that an MCP server's connection state changed out-of-band.

    Emitted by the startup warm-up as each server finishes, so an open Settings
    panel reflects the real state (and tool count) as it resolves instead of
    sitting on the "connecting" placeholder until the user reloads.
    """
    await _bus.publish(
        {"type": "mcp.server_state", "server": server, "state": state, "tools": tools}
    )


async def publish_mcp_auth_url(server: str, url: str) -> None:
    """Hand the frontend the interactive OAuth authorization URL to open.

    The interactive sign-in emits this so the window that started it can steer a
    *script-opened* popup at ``url``. That matters because only a window a script
    opened can later be closed by script — the loopback callback page then closes
    itself once auth completes, instead of stranding a browser tab that the
    backend's ``webbrowser.open`` fallback would leave behind. Broadcast to every
    window; only the one with a pending popup acts on it. The URL carries a
    single-use PKCE ``state``, not a bearer secret, and this is a localhost app.
    """
    await _bus.publish({"type": "mcp.auth_url", "server": server, "url": url})


async def publish_mcp_auth_resolved(server: str) -> None:
    """Announce that an MCP server's interactive sign-in has been renewed.

    A sign-in completed in *one* window (its script-opened popup, the OS-browser
    tab the hands-free flow self-opens, or a silent pass) leaves every *other*
    window still showing the stale ``McpAuthBanner`` — they never made the
    request, so nothing tells them the credentials are fresh again. Broadcasting
    this once the re-auth succeeds lets those windows drop the banner (and any
    "Signing in…" state) without a page reload. Not client-id filtered: the
    originating window has already cleared locally, and the rest are precisely
    who this is for.
    """
    await _bus.publish({"type": "mcp.auth_resolved", "server": server})


async def publish_message_changed_chat(chat_id: int) -> None:
    await _bus.publish({"type": "message.changed", "chat_id": chat_id})


async def publish_stream_started(topic_id: int) -> None:
    await _bus.publish({"type": "stream.started", "topic_id": topic_id})


async def publish_stream_started_chat(chat_id: int) -> None:
    await _bus.publish({"type": "stream.started", "chat_id": chat_id})


async def publish_stream_ended(topic_id: int) -> None:
    await _bus.publish({"type": "stream.ended", "topic_id": topic_id})


async def publish_stream_ended_chat(chat_id: int) -> None:
    await _bus.publish({"type": "stream.ended", "chat_id": chat_id})


async def publish_reminder_changed(
    *, topic_id: int | None = None, chat_id: int | None = None
) -> None:
    """Signal that the set of reminders changed (created / fired / cleared).

    Carries the affected container id so a window viewing it can react; the
    sidebar Reminders section reloads regardless.
    """
    await _bus.publish({"type": "reminder.changed", "topic_id": topic_id, "chat_id": chat_id})


async def publish_agent_changed(
    *,
    agent_session_id: int | None = None,
    topic_id: int | None = None,
    chat_id: int | None = None,
    agent_run_id: int | None = None,
) -> None:
    """Signal that an agent session's state or event stream changed.

    Carries the agent session id (so the Agents tab / an open session view can
    react) plus the linked container, if any, so a window viewing that topic or
    chat can refresh its agent badge. ``agent_run_id`` names the *execution* the
    change came from — the same agent can be driven by two workflows at once, so
    a view scoped to one run can ignore the other's traffic.
    """
    await _bus.publish(
        {
            "type": "agent.changed",
            "agent_session_id": agent_session_id,
            "agent_run_id": agent_run_id,
            "topic_id": topic_id,
            "chat_id": chat_id,
        }
    )


async def publish_read_changed(
    *,
    topic_id: int | None = None,
    chat_id: int | None = None,
    agent_session_id: int | None = None,
) -> None:
    """Signal that a conversation was marked read (its ``last_read_at`` advanced).

    Emitted by the ``/read`` endpoints so *other* tabs clear the unread badge and
    counter for the same discussion in real time. It carries only the affected
    id; receivers re-fetch that section's unread state. Deliberately distinct
    from ``message.changed`` and never triggers a re-mark, so it can't loop with
    the "mark the actively-viewed conversation read" logic. Echo-filtered for the
    originating tab, which already updated optimistically.
    """
    await _bus.publish(
        {
            "type": "read.changed",
            "topic_id": topic_id,
            "chat_id": chat_id,
            "agent_session_id": agent_session_id,
        }
    )


async def publish_meeting_changed(meeting_session_id: int | None = None) -> None:
    """Signal that the set of meeting sessions (or one session) changed.

    Emitted by the ``/api/live`` endpoints so other windows refresh the Live
    section list in real time (created / renamed / ended / deleted). Carries the
    affected session id when it applies to a single session.
    """
    await _bus.publish({"type": "meeting.changed", "meeting_session_id": meeting_session_id})


async def publish_workflow_changed(
    workflow_id: int | None = None,
    *,
    status: str | None = None,
    name: str | None = None,
) -> None:
    """Signal that a workflow's definition or run state changed.

    Emitted by the workflow router + coordinator so the Workflows tab (gallery
    and any open workflow view) refreshes in real time as steps advance, the
    status flips, or the pipeline is edited. Carries the affected workflow id;
    receivers re-fetch. The per-step agent state still arrives on the existing
    ``agent.changed`` channel, so an open step modal updates independently.

    ``status``/``name`` ride along so the client can *notify* on the transitions
    that matter — a run finishing, failing, or parking on a human approval —
    without fetching the workflow first. A workflow that quietly parks waiting
    for a decision is otherwise invisible until someone opens the tab.
    """
    await _bus.publish(
        {
            "type": "workflow.changed",
            "workflow_id": workflow_id,
            "status": status,
            "name": name,
        }
    )
