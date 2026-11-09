"""SDK event handling: queue each run's events in arrival order, archive them
into the timeline, feed the live stream, and turn them into run state.

``AgentManager`` delegates here; this module must not import ``manager`` at
runtime.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from precursor.backend.models import AgentRun, AgentSession
from precursor.backend.schemas.agent import AgentEvent
from precursor.backend.services.agents import timeline
from precursor.backend.services.agents.directives import (
    RESULT_SUMMARY_CAP,
    strip_control_directives,
)
from precursor.backend.services.agents.event_normalizer import is_content_free, normalize_event
from precursor.backend.services.agents.live_session import _LiveSession
from precursor.backend.services.agents.live_stream import StreamUpdate
from precursor.backend.services.events import publish_agent_changed
from precursor.backend.services.model_fallbacks import is_model_rejection

if TYPE_CHECKING:
    from precursor.backend.services.agents.manager import AgentManager

logger = logging.getLogger(__name__)

# An agent reaching one of these has ended its turn, which is what a workflow
# waits on to advance. ``needs_approval`` is included because the coordinator
# uses that pass to surface the approve/deny card on the workflow board, not
# because it moves the run on.
_RESTING_STATUSES = (
    "idle",
    "completed",
    "failed",
    "blocked",
    "cancelled",
    "needs_approval",
)

# Streaming frames aren't archived, but a long stretch of them is still the run
# being alive: stamp its activity this often so the watchdog (whose timeout
# floors at 30 s) never takes a model thinking at length for a stall.
_STREAM_ACTIVITY_INTERVAL_SECONDS = 10.0

# How often streamed thinking/answer text is announced to open views. The
# frontend coalesces refreshes anyway; this keeps a fast stream from costing a
# bus message per token.
_STREAM_PUBLISH_INTERVAL_SECONDS = 0.25


# Event kinds that show the model has produced output for the current turn, so a
# model refusal can no longer be recovered by retrying on another model.
_MODEL_OUTPUT_KINDS = frozenset(
    {
        "assistant_delta",
        "reasoning_delta",
        "assistant_message",
        "reasoning",
        "tool_call",
        "tool_result",
        "usage",
    }
)

# A trailing idle must not mask these: the outcome stays visible and any
# in-flight prompt stays resumable.
_STICKY_STATUSES = ("needs_approval", "cancelled", "failed", "blocked", "completed")


def _note_model_progress(live: _LiveSession, kind: str) -> None:
    if kind == "turn_start":
        live.model_recovering = False
    if (
        kind in _MODEL_OUTPUT_KINDS
        or ("Tool" in kind and kind != "SessionToolsUpdatedData")
        or "Permission" in kind
    ):
        live.model_output_started = True
        live.model_recovering = False


def _can_retry_model(live: _LiveSession, rejection: str) -> bool:
    """Whether a refused model can be swapped for an untried one, nothing lost."""
    return bool(
        not live.model_output_started
        and not live.model_retry_scheduled
        and live.dispatched_prompt
        and is_model_rejection(rejection)
        and any(c not in live.model_attempts for c in live.model_candidates)
    )


class EventHandler:
    def __init__(self, manager: AgentManager) -> None:
        # A back-reference read at call time (never captured bound methods), so a
        # test patching the manager still takes effect.
        self._manager = manager

    def dispatch(self, agent_id: int, run_id: int, event: Any) -> None:
        """Queue one SDK event for handling strictly in arrival order.

        This used to spawn one task per event. Each task awaited ``_load_run``
        before taking the agent's event lock, so a burst of events reached the
        lock in whatever order those reads finished. At the end of a turn that
        meant ``session.idle`` could be handled before ``turn_end`` and the final
        ``assistant.message``. The run then rested on the *previous* message
        (often a tool-call-only one with no text), and a workflow advanced
        with an empty or stale step output while the real answer arrived a
        moment later.
        """
        m = self._manager
        live = m._live.get(run_id)
        name = type(getattr(event, "data", event)).__name__
        if live is not None and (
            name
            in {
                "AssistantMessageData",
                "AssistantMessageDeltaData",
                "AssistantReasoningData",
                "AssistantReasoningDeltaData",
                "AssistantUsageData",
            }
            or ("Tool" in name and name != "SessionToolsUpdatedData")
        ):
            # Mark at receipt, before an awaited send can fail while this event
            # is still queued. Recovery must never replay already emitted work.
            live.model_output_started = True
        queue = m._event_queues.setdefault(agent_id, deque())
        queue.append((run_id, event))
        if agent_id not in m._event_drainers:
            m._event_drainers.add(agent_id)
            m._spawn(self.drain(agent_id))

    async def drain(self, agent_id: int) -> None:
        """Handle an agent's queued SDK events one at a time, oldest first."""
        m = self._manager
        queue = m._event_queues.get(agent_id)
        try:
            while queue:
                run_id, event = queue.popleft()
                try:
                    await self.handle(run_id, event)
                except Exception:
                    # One bad event must not stall every event queued behind it.
                    logger.exception("failed to handle %s for run %s", type(event).__name__, run_id)
        finally:
            # No await separates the empty check above from here, so an event
            # dispatched meanwhile can't be stranded without a drainer.
            m._event_drainers.discard(agent_id)
            if not queue:
                m._event_queues.pop(agent_id, None)

    async def handle(self, run_id: int, event: Any) -> None:
        # Events arrive keyed by the *run* that produced them, but are serialised
        # per *agent*: the idle handler must run after the assistant-message
        # handler has committed ``result_summary`` (otherwise ``_notify_back``
        # posts the previous turn's answer), and ``_notify_back`` writes into the
        # agent's shared topic/chat, so concurrent runs of one agent must take
        # turns even though their sessions are independent.
        m = self._manager
        loaded = await m._load_run(run_id)
        if loaded is None:
            return
        lock = m._event_locks.setdefault(loaded[1].id, asyncio.Lock())
        async with lock:
            await m._handle_event_locked(run_id, event)

    async def handle_locked(self, run_id: int, event: Any) -> None:
        m = self._manager
        loaded = await m._load_run(run_id)
        if loaded is None:
            return
        run, agent = loaded
        agent_id = agent.id
        # Archive every event with content so the timeline persists across
        # session teardown (e.g. on topic link) and process restart, where the SDK
        # would otherwise drop it (``get_events`` only replays ``SessionStartData``
        # on resume). Streaming frames only feed the live stream.
        await m._transcript.ensure_loaded(agent_id)
        # The status *before* this event is handled, read up front rather than
        # around the final patch below: handlers reached from here (``_on_idle``,
        # and ``UsageMeter.enforce_budget`` via ``_record_usage``) commit status changes of
        # their own mid-flight, and those are transitions the workflow seam must
        # still see. Read from the *run* — a sibling execution's status must not
        # look like this one transitioning.
        before_status = run.status
        normalised = normalize_event(event)
        normalised.at = datetime.now(UTC)
        # Stamp the producing run so the timeline can be split per execution.
        normalised.agent_run_id = run_id
        live = m._live.get(run_id)
        if live is not None:
            _note_model_progress(live, normalised.kind)
        update = live.stream.observe(normalised) if live is not None else None
        if update is not None and update.flush is not None:
            await m._record(agent_id, update.flush)
        if is_content_free(normalised.kind, normalised.text):
            # A streaming frame, or a message/reasoning with nothing in it. The
            # live stream above has taken what it needs; the archive gets none.
            await self.on_stream_frame(agent_id, run_id, live, update)
            return
        await m._record(agent_id, normalised)

        # A workiq tool that errors after the session was built with valid creds
        # usually means the OAuth token lapsed mid-turn. Surface the same sign-in
        # prompt as the pre-flight gate so the user can re-authenticate inline
        # instead of reading a raw tool failure. Best-effort: only fires when the
        # event carries a server name and the creds are actually gone.
        auth_server = await m._auth_server_from_failed_tool(normalised)
        if auth_server is not None:
            await m._announce_auth_required(agent_id, [auth_server])

        data = getattr(event, "data", event)
        patch: dict[str, Any] = {"last_activity_at": datetime.now(UTC)}
        if await self._apply(run, agent, live, data, patch):
            await self._settle(run_id, agent_id, before_status, patch)

    async def _apply(
        self,
        run: AgentRun,
        agent: AgentSession,
        live: _LiveSession | None,
        data: Any,
        patch: dict[str, Any],
    ) -> bool:
        """Fold one SDK event into ``patch``; ``False`` stops handling it here."""
        m = self._manager
        name = type(data).__name__
        if name == "SessionStartData":
            # The only place the SDK hands us the conversation's resume handle.
            # Persisting it is what lets a later session rebuild (an OAuth
            # refresh, a changed MCP catalogue, a recovered sign-in) resume this
            # same conversation instead of silently starting an empty one and
            # dropping the agent's task prompt and history.
            sid = getattr(data, "session_id", None)
            if sid and not run.copilot_session_id:
                await m._patch_run(run.id, copilot_session_id=str(sid))
        elif name == "AssistantMessageData":
            self._on_message(run.id, data, patch)
        elif name == "AssistantUsageData":
            await m._record_usage(run.id, data)
        elif name in ("SessionIdleData", "SystemNotificationAgentIdle"):
            if live is not None and live.model_recovering:
                return False
            fresh = await m._run(run.id)
            # Don't let a trailing idle event mask a turn that just errored, was
            # paused/cancelled, or already reached a terminal/blocked resting
            # state — those statuses are sticky so the outcome stays visible (and
            # any in-flight prompt stays resumable).
            if fresh is not None and fresh.status not in _STICKY_STATUSES:
                # The goal loop decides the resting status (idle/blocked/completed)
                # or continues autonomously; it mutates ``patch`` and reposts only
                # at rest transitions.
                await m._on_idle(agent, fresh, patch)
        elif name in ("AbortData",):
            patch["status"] = "cancelled"
            patch["finished_at"] = datetime.now(UTC)
        elif name in ("ErrorData", "SessionErrorData"):
            return await self._on_error(run.id, agent.id, live, data, patch)
        return True

    def _on_message(self, run_id: int, data: Any, patch: dict[str, Any]) -> None:
        content = getattr(data, "content", None)
        if not content:
            return
        # Scrub control directives from the *displayed* summary; keep the raw
        # message in ``pending_answer`` for the topic repost and for
        # directive/gate parsing downstream.
        patch["result_summary"] = strip_control_directives(str(content))[:RESULT_SUMMARY_CAP]
        # Keep the full answer for the topic/chat repost — the summary column is
        # capped for the agent list.
        live = self._manager._live.get(run_id)
        if live is not None:
            live.pending_answer = str(content)

    async def _on_error(
        self,
        run_id: int,
        agent_id: int,
        live: _LiveSession | None,
        data: Any,
        patch: dict[str, Any],
    ) -> bool:
        """Fail the run, unless a different model can still take the turn."""
        m = self._manager
        message = str(getattr(data, "message", type(data).__name__))
        rejection = f"{message} {getattr(data, 'error_code', '')}"
        if live is not None and live.model_retry_scheduled and is_model_rejection(rejection):
            # SDKs can report the same refused attempt through both error
            # event names. A queued recovery already owns that attempt.
            return False
        if live is not None and _can_retry_model(live, rejection):
            live.model_recovering = True
            live.model_retry_scheduled = True
            m.enqueue(
                m._model_selection.recover(run_id, live, rejection, turn_id=live.model_turn_id)
            )
            return False
        patch["status"] = "failed"
        patch["error"] = message[:2000]
        patch["finished_at"] = datetime.now(UTC)
        # Auto-recovery: if a retry budget remains, arm a backoff re-run the
        # scheduler will pick up. Keeps a flaky turn from parking the fleet.
        # The budget is cumulative governance and lives on the definition.
        failed_agent = await m._load(agent_id)
        if failed_agent is not None and failed_agent.retry_count < failed_agent.max_retries:
            patch["next_retry_at"] = m._retry_due_at(failed_agent.retry_count)
        return True

    async def _settle(
        self, run_id: int, agent_id: int, before_status: str, patch: dict[str, Any]
    ) -> None:
        """Write the event's outcome, announce it, and advance a resting run's workflow."""
        m = self._manager
        await m._patch_run(run_id, **patch)
        after = await m._run(run_id)
        # Re-read the definition: ``_patch_run`` expired it, and the notification
        # needs its current topic/chat routing.
        owner = await m._load(agent_id)
        await publish_agent_changed(
            agent_session_id=agent_id,
            topic_id=owner.topic_id if owner else None,
            chat_id=owner.chat_id if owner else None,
            agent_run_id=run_id,
        )
        # Workflow coordination advances when this run *transitions into* a
        # resting/terminal state — a workflow chains plain agents itself. Testing
        # the status alone would re-fire for every subsequent event an already
        # resting agent emits (pending-message, MCP-status and tool-list updates
        # all arrive after a turn ends), and each of those advances raced the
        # others into re-entering the same step: duplicate trace rows, a second
        # real ``start_task``, and double-counted tokens.
        if (
            after is not None
            and after.status != before_status
            and after.status in _RESTING_STATUSES
        ):
            m.enqueue(m._advance_workflows(run_id))

    async def record(self, agent_id: int, event: AgentEvent) -> None:
        """Append an event to the agent's timeline, in memory and in the archive."""
        m = self._manager
        m._events.setdefault(agent_id, []).append(event)
        await timeline.archive_event(agent_id, event, agent_run_id=event.agent_run_id)

    async def on_stream_frame(
        self,
        agent_id: int,
        run_id: int,
        live: _LiveSession | None,
        update: StreamUpdate | None,
    ) -> None:
        """Keep a run that is only streaming alive, and tell open views, cheaply."""
        m = self._manager
        if live is None or update is None:
            return
        stream = live.stream
        now = time.monotonic()
        if now - stream.touched >= _STREAM_ACTIVITY_INTERVAL_SECONDS:
            stream.touched = now
            await m._patch_run(run_id, last_activity_at=datetime.now(UTC))
        # A change held back by the throttle goes out with a later frame; byte
        # counters interleave with every token, so it never waits for long.
        stream.dirty = stream.dirty or update.changed
        # Recovered thinking was just archived: the timeline has a new node.
        due = now - stream.published >= _STREAM_PUBLISH_INTERVAL_SECONDS
        if stream.dirty and (due or update.flush is not None):
            stream.published = now
            stream.dirty = False
            await publish_agent_changed(agent_session_id=agent_id, agent_run_id=run_id)
