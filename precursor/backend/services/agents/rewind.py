"""Rewind an agent's current run to an earlier prompt (issue #401).

An agent's conversation is owned by the Copilot SDK session; Precursor only
keeps a normalised copy of it in ``agent_events`` for display. A rewind must
therefore cut both, in that order: the SDK history first (through the
experimental ``rpc.history.rewind``), so the model stops seeing the dropped
turns, then the archive and the run state derived from those turns.

Conversation only, and irreversible: the SDK can't undo a rewind, so the UI
asks for explicit confirmation instead of offering an undo grace. Only the
current run's prompts can be cut — an earlier run is a different SDK session.

Agent exchanges mirrored into a topic or chat are left alone: those are
separate messages, rewound (or not) on their own side.

Must not import ``manager`` at runtime.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select

from precursor.backend.db import SessionLocal
from precursor.backend.models import AgentEventRecord
from precursor.backend.models.agent_artifact import AgentArtifact
from precursor.backend.schemas.agent import AgentEvent, AgentRewindResult
from precursor.backend.services.agents.directives import (
    RESULT_SUMMARY_CAP,
    strip_control_directives,
)

if TYPE_CHECKING:
    from precursor.backend.services.agents.manager import AgentManager

logger = logging.getLogger(__name__)

# A turn is in flight (or about to be): the SDK would truncate a history that
# is still being appended to.
_BUSY_STATUSES = frozenset({"running", "needs_approval", "pending"})

# ``session-busy`` is transient: the SDK answers the same request once the
# session settles, typically within moments of a turn ending.
_BUSY_RETRIES = 3
_BUSY_RETRY_DELAY_SECONDS = 0.5
_REWIND_TIMEOUT_SECONDS = 60.0

# Outcomes after which the conversation *was* truncated. The cleanup ones only
# mean the SDK couldn't tidy its own snapshots afterwards.
_TRUNCATED = frozenset({"success", "checkpoint-cleanup-failed", "snapshot-prune-failed"})

_FAILURES: dict[str, tuple[int, str]] = {
    "unsupported-remote-session": (409, "Rewind isn't available for a remote agent session."),
    "file-change-tracking-disabled": (
        409,
        "This agent session doesn't track file changes, so they can't be restored.",
    ),
    "truncation-failed": (
        502,
        "The agent's history couldn't be truncated; the conversation was left as it was.",
    ),
    "files-rolled-back": (
        502,
        "Restoring files failed and was rolled back; the conversation was left as it was.",
    ),
    "rollback-incomplete": (
        502,
        "Restoring files failed part-way and some files couldn't be put back. "
        "Check the agent's working directory.",
    ),
}


class RewindError(ValueError):
    """A rewind was refused or failed; ``status_code`` is the HTTP mapping."""

    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


def _value(member: Any) -> Any:
    # SDK enums and plain strings (test doubles) compare alike.
    return getattr(member, "value", member)


async def _retry_while_busy(call: Callable[[], Awaitable[Any]], busy: Callable[[Any], bool]) -> Any:
    for attempt in range(_BUSY_RETRIES):
        result = await call()
        if not busy(result):
            return result
        if attempt < _BUSY_RETRIES - 1:
            await asyncio.sleep(_BUSY_RETRY_DELAY_SECONDS)
    raise RewindError("The agent session is still settling. Try again in a moment.")


async def rewind(manager: AgentManager, agent_id: int, event_id: str) -> AgentRewindResult:
    """Drop the prompt ``event_id`` and everything after it from the current run."""
    try:
        event_id = str(uuid.UUID(event_id))
    except ValueError as exc:
        raise RewindError("That isn't a rewind point.", status_code=400) from exc

    agent = await manager._load(agent_id)
    if agent is None:
        raise RewindError("Agent not found.", status_code=404)
    run = await manager._resolve_run(agent_id)
    if run is None or (not run.copilot_session_id and run.id not in manager._live):
        raise RewindError("Nothing to rewind yet: this agent has no conversation.")
    if run.status in _BUSY_STATUSES:
        raise RewindError("The agent is busy. Wait for it to finish, or stop it, first.")

    await manager._transcript.ensure_loaded(agent_id)
    if _cut_index(manager._events.get(agent_id, []), run.id, event_id) is None:
        raise RewindError(
            "Only prompts from the agent's current run can be rewound.", status_code=400
        )

    try:
        from copilot.generated.rpc import HistoryRewindMode, HistoryRewindRequest
    except ImportError as exc:  # pragma: no cover - depends on the installed SDK
        raise RewindError(
            "Rewind needs github-copilot-sdk 1.0.11 or newer.", status_code=501
        ) from exc

    live = await manager._ensure_live(agent, run)
    history = live.sdk_session.rpc.history
    try:
        listing = await _retry_while_busy(
            history.list_rewind_points,
            lambda r: _value(r.unavailable_reason) == "session-busy",
        )
    except RewindError:
        raise
    except Exception as exc:
        logger.warning("listing rewind points failed for agent %s", agent_id, exc_info=True)
        raise RewindError(f"Rewind failed: {exc}", status_code=502) from exc
    reason = _value(listing.unavailable_reason)
    if reason in _FAILURES:
        status_code, message = _FAILURES[reason]
        raise RewindError(message, status_code=status_code)
    if event_id not in {p.event_id for p in listing.points}:
        raise RewindError(
            "The agent's session no longer has this turn to return to "
            "(it may have been compacted into a summary)."
        )

    # Holding the agent's event lock keeps the SDK cut and the archive cut
    # together: an event handled in between would land past the boundary and
    # survive in the archive while the SDK has already dropped its turn.
    lock = manager._event_locks.setdefault(agent_id, asyncio.Lock())
    async with lock:
        # A turn may have started while the session was being prepared.
        fresh = await manager._run(run.id)
        if fresh is None or fresh.status in _BUSY_STATUSES:
            raise RewindError("The agent is busy. Wait for it to finish, or stop it, first.")
        try:
            result = await _retry_while_busy(
                lambda: history.rewind(
                    HistoryRewindRequest(event_id=event_id, mode=HistoryRewindMode.CONVERSATION),
                    timeout=_REWIND_TIMEOUT_SECONDS,
                ),
                lambda r: _value(r.outcome) == "session-busy",
            )
        except RewindError:
            raise
        except Exception as exc:
            logger.warning("rewind failed for agent %s", agent_id, exc_info=True)
            raise RewindError(f"Rewind failed: {exc}", status_code=502) from exc
        outcome = _value(result.outcome)
        if outcome not in _TRUNCATED:
            status_code, message = _FAILURES.get(
                outcome, (502, f"Rewind failed ({outcome}); the conversation was left as it was.")
            )
            if getattr(result, "error", None):
                message = f"{message} ({result.error})"
            raise RewindError(message, status_code=status_code)
        if outcome != "success":
            logger.warning(
                "rewind of agent %s truncated history but reported %s: %s",
                agent_id,
                outcome,
                getattr(result, "error", None),
            )
        removed, boundary_at = await _truncate_archive(manager, agent_id, run.id, event_id)
        manager._transcript.bump_epoch(agent_id)
        await _reset_run_state(manager, agent_id, run.id, boundary_at)

    await manager._publish(agent_id, agent_run_id=run.id)
    return AgentRewindResult(
        events_removed=removed, sdk_events_removed=getattr(result, "events_removed", None)
    )


def _cut_index(events: list[AgentEvent], run_id: int, event_id: str) -> int | None:
    for i, ev in enumerate(events):
        if ev.event_id == event_id and ev.agent_run_id == run_id:
            return i
    return None


async def _truncate_archive(
    manager: AgentManager, agent_id: int, run_id: int, event_id: str
) -> tuple[int, datetime | None]:
    """Drop the run's events from the prompt ``event_id`` on, in memory and on disk.

    Another run's events past the boundary are kept: a concurrent execution of
    the same agent has its own SDK session, which this rewind didn't touch.
    """
    events = manager._events.get(agent_id, [])
    cut = _cut_index(events, run_id, event_id)
    if cut is None:
        return 0, None
    boundary_at = events[cut].at
    kept = events[:cut] + [ev for ev in events[cut:] if ev.agent_run_id != run_id]
    manager._events[agent_id] = kept
    async with SessionLocal() as session:
        # ``event_id`` was normalised to a UUID above, so it carries no LIKE
        # wildcards; the payload is compact JSON written by ``model_dump_json``.
        boundary = await session.scalar(
            select(func.min(AgentEventRecord.id)).where(
                AgentEventRecord.agent_session_id == agent_id,
                AgentEventRecord.agent_run_id == run_id,
                AgentEventRecord.payload.like(f'%"event_id":"{event_id}"%'),
            )
        )
        if boundary is not None:
            await session.execute(
                delete(AgentEventRecord).where(
                    AgentEventRecord.agent_session_id == agent_id,
                    AgentEventRecord.agent_run_id == run_id,
                    AgentEventRecord.id >= boundary,
                )
            )
            await session.commit()
    return len(events) - len(kept), boundary_at


async def _reset_run_state(
    manager: AgentManager, agent_id: int, run_id: int, boundary_at: datetime | None
) -> None:
    """Return the run to rest, with only what the kept turns produced.

    The dropped turns' answer, progress, raised question and goal-loop counters
    go with them; the summary falls back to the last kept answer. Artifacts the
    run published from the dropped prompt on are deleted.

    Sends don't take the event lock, so a turn can start right after the cut.
    Its status, prompt and live answer are then left alone: they are the new
    turn's, not the dropped ones'.
    """
    last_answer = next(
        (
            ev.text
            for ev in reversed(manager._events.get(agent_id, []))
            if ev.agent_run_id == run_id and ev.kind == "assistant_message" and ev.text
        ),
        None,
    )
    patch: dict[str, Any] = {
        "blocked_question": None,
        "result_summary": (
            strip_control_directives(last_answer)[:RESULT_SUMMARY_CAP] if last_answer else None
        ),
        "step_count": 0,
        "progress": None,
        "progress_label": None,
        "stall_count": 0,
        "last_progress": None,
        "next_retry_at": None,
    }
    fresh = await manager._run(run_id)
    resting = fresh is not None and fresh.status not in _BUSY_STATUSES
    if resting:
        patch.update(status="idle", active_prompt=None, error=None, finished_at=None)
    await manager._patch_run(run_id, **patch)
    live = manager._live.get(run_id)
    if live is not None and resting:
        live.pending_answer = None
        live.last_progress = None
        live.stall_count = 0
    if boundary_at is None:
        return
    async with SessionLocal() as session:
        await session.execute(
            delete(AgentArtifact).where(
                AgentArtifact.agent_run_id == run_id,
                AgentArtifact.created_at >= boundary_at,
            )
        )
        await session.commit()
