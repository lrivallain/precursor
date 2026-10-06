"""Rewind an agent's current run to an earlier prompt (issue #401).

An agent's conversation is owned by the Copilot SDK session; Precursor only
keeps a normalised copy of it in ``agent_events`` for display. A rewind must
therefore cut both, in that order: the SDK history first (through the
experimental ``rpc.history.rewind``), so the model stops seeing the dropped
turns, then the archive and the run state derived from those turns.

Irreversible: the SDK can't undo a rewind, so the UI asks for explicit
confirmation instead of offering an undo grace. In ``conversation-and-files``
mode the SDK also puts back the files the dropped turns changed, which needs a
session that tracked file changes from its first turn (see :func:`preview`).

Only the current SDK session's prompts can be cut: the current run's, and an
earlier run's that continued the same session (an edited task restarts on a
new run but keeps the conversation). Runs driven by a workflow step are left
to the workflow, which has already passed their output downstream.

Agent exchanges mirrored into a topic or chat are left alone: those are
separate messages, rewound (or not) on their own side.

Must not import ``manager`` at runtime.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select

from precursor.backend.db import SessionLocal
from precursor.backend.models import AgentEventRecord, AgentRun, AgentSession
from precursor.backend.models.agent_artifact import AgentArtifact
from precursor.backend.schemas.agent import (
    AgentEvent,
    AgentRewindFile,
    AgentRewindMode,
    AgentRewindPreview,
    AgentRewindResult,
    AgentRewindSkippedFile,
)
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
# A file restore rewrites files on disk before truncating; give it longer.
_FILES_REWIND_TIMEOUT_SECONDS = 180.0
_PREVIEW_TIMEOUT_SECONDS = 30.0

# Outcomes after which the conversation *was* truncated. The cleanup ones only
# mean the SDK couldn't tidy its own snapshots afterwards.
_TRUNCATED = frozenset(
    {"success", "checkpoint-cleanup-failed", "snapshot-prune-failed", "unconfirmed"}
)

_TRACKING_DISABLED = "file-change-tracking-disabled"

_FAILURES: dict[str, tuple[int, str]] = {
    "unsupported-remote-session": (409, "Rewind isn't available for a remote agent session."),
    _TRACKING_DISABLED: (
        409,
        "This agent session doesn't track file changes, so its files can't be restored. "
        "Rewind the conversation only.",
    ),
    "truncation-failed": (
        502,
        "The agent's history couldn't be truncated; the conversation was left as it was.",
    ),
    "files-rolled-back": (
        502,
        "Restoring files failed, so they were all put back as they were; "
        "the conversation was left as it was.",
    ),
    "rollback-incomplete": (
        502,
        "Restoring files failed part-way and some files couldn't be put back. "
        "The conversation was left as it was; check the agent's files before retrying.",
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


def _rpc() -> Any:
    try:
        from copilot.generated import rpc
    except ImportError as exc:  # pragma: no cover - depends on the installed SDK
        raise RewindError(
            "Rewind needs github-copilot-sdk 1.0.11 or newer.", status_code=501
        ) from exc
    if not hasattr(rpc, "HistoryRewindRequest"):  # pragma: no cover - older SDK
        raise RewindError("Rewind needs github-copilot-sdk 1.0.11 or newer.", status_code=501)
    return rpc


async def _retry_while_busy(call: Callable[[], Awaitable[Any]], busy: Callable[[Any], bool]) -> Any:
    for attempt in range(_BUSY_RETRIES):
        result = await call()
        if not busy(result):
            return result
        if attempt < _BUSY_RETRIES - 1:
            await asyncio.sleep(_BUSY_RETRY_DELAY_SECONDS)
    raise RewindError("The agent session is still settling. Try again in a moment.")


async def _sdk_call(agent_id: int, what: str, call: Awaitable[Any]) -> Any:
    try:
        return await call
    except RewindError:
        raise
    except Exception as exc:
        logger.warning("%s failed for agent %s", what, agent_id, exc_info=True)
        raise RewindError(f"Rewind failed: {exc}", status_code=502) from exc


async def session_run_ids(run: AgentRun) -> frozenset[int]:
    """The runs whose prompts belong to ``run``'s SDK conversation.

    Empty for a workflow-driven run. ``restart_with_task`` opens a run that
    inherits the previous handle, so those earlier prompts are still in the
    SDK's history. A ``/clear`` that kept the handle wiped the archive, so
    older runs sharing it contribute no prompts.
    """
    if run.workflow_run_id is not None:
        return frozenset()
    if not run.copilot_session_id:
        return frozenset({run.id})
    async with SessionLocal() as session:
        ids = (
            await session.execute(
                select(AgentRun.id).where(
                    AgentRun.agent_id == run.agent_id,
                    AgentRun.copilot_session_id == run.copilot_session_id,
                    AgentRun.workflow_run_id.is_(None),
                )
            )
        ).scalars()
        return frozenset({run.id, *ids})


async def rewindable_run_ids(agent_id: int) -> list[int]:
    """:func:`session_run_ids` of the agent's current run, for the transcript page.

    Reads ``current_run_id`` directly rather than resolving a run, so a page
    read never opens one.
    """
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        run_id = agent.current_run_id if agent is not None else None
        run = await session.get(AgentRun, run_id) if run_id is not None else None
    return sorted(await session_run_ids(run)) if run is not None else []


@dataclass
class _Target:
    run: AgentRun
    run_ids: frozenset[int]
    event_id: str
    history: Any
    file_tracking: bool


async def _prepare(manager: AgentManager, agent_id: int, event_id: str) -> _Target:
    """Check ``event_id`` can be rewound to now, and open the SDK session for it."""
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
    if run.workflow_run_id is not None:
        raise RewindError(
            "This run is driven by a workflow step, so it can't be rewound here. "
            "Replay the step from its workflow instead."
        )
    if run.status in _BUSY_STATUSES:
        raise RewindError("The agent is busy. Wait for it to finish, or stop it, first.")

    run_ids = await session_run_ids(run)
    await manager._transcript.ensure_loaded(agent_id)
    if _cut_index(manager._events.get(agent_id, []), run_ids, event_id) is None:
        raise RewindError(
            "Only prompts from the agent's current session can be rewound.", status_code=400
        )

    _rpc()
    live = await manager._ensure_live(agent, run)
    history = live.sdk_session.rpc.history
    listing = await _sdk_call(
        agent_id,
        "listing rewind points",
        _retry_while_busy(
            history.list_rewind_points,
            lambda r: _value(r.unavailable_reason) == "session-busy",
        ),
    )
    reason = _value(listing.unavailable_reason)
    if reason in _FAILURES:
        status_code, message = _FAILURES[reason]
        raise RewindError(message, status_code=status_code)
    if event_id not in {p.event_id for p in listing.points}:
        raise RewindError(
            "The agent's session no longer has this turn to return to "
            "(it may have been compacted into a summary)."
        )
    return _Target(
        run=run,
        run_ids=run_ids,
        event_id=event_id,
        history=history,
        file_tracking=bool(getattr(listing, "file_change_tracking_enabled", False)),
    )


async def preview(manager: AgentManager, agent_id: int, event_id: str) -> AgentRewindPreview:
    """What a ``conversation-and-files`` rewind to ``event_id`` would restore.

    Validates the turn exactly like :func:`rewind`, so a preview that answers
    is one the rewind would accept. This is where an idle agent's SDK session
    gets resumed: the user has asked to rewind it.
    """
    target = await _prepare(manager, agent_id, event_id)
    if not target.file_tracking:
        return AgentRewindPreview(event_id=target.event_id, unavailable_reason=_TRACKING_DISABLED)
    rpc = _rpc()
    result = await _sdk_call(
        agent_id,
        "previewing a rewind",
        _retry_while_busy(
            lambda: target.history.preview_rewind(
                rpc.HistoryPreviewRewindRequest(event_id=target.event_id),
                timeout=_PREVIEW_TIMEOUT_SECONDS,
            ),
            lambda r: not r.available and _value(r.reason) == "session-busy",
        ),
    )
    return AgentRewindPreview(
        event_id=target.event_id,
        file_tracking=True,
        files_available=bool(result.available),
        unavailable_reason=None if result.available else _value(result.reason),
        file_count=int(result.file_count or 0),
        files=[
            AgentRewindFile(
                path=f.path,
                change_type=str(_value(f.change_type)),
                lines_added=int(f.lines_added or 0),
                lines_removed=int(f.lines_removed or 0),
            )
            for f in result.files or []
        ],
    )


def _failure_message(outcome: str, result: Any, *, files: bool) -> tuple[int, str]:
    status_code, message = _FAILURES.get(
        outcome, (502, f"Rewind failed ({outcome}); the conversation was left as it was.")
    )
    restored = list(getattr(result, "restored_files", None) or [])
    if outcome == "truncation-failed" and files and restored:
        # Restore lands before truncation and is idempotent, so running the
        # same rewind again is the SDK's recovery path.
        message += (
            f" {len(restored)} file(s) were already restored; rewinding again finishes the cut."
        )
    if getattr(result, "error", None):
        message = f"{message} ({result.error})"
    return status_code, message


async def rewind(
    manager: AgentManager,
    agent_id: int,
    event_id: str,
    mode: AgentRewindMode = "conversation",
) -> AgentRewindResult:
    """Drop the prompt ``event_id`` and everything after it from the current session."""
    target = await _prepare(manager, agent_id, event_id)
    files = mode == "conversation-and-files"
    if files and not target.file_tracking:
        status_code, message = _FAILURES[_TRACKING_DISABLED]
        raise RewindError(message, status_code=status_code)
    rpc = _rpc()
    run = target.run

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
                lambda: target.history.rewind(
                    rpc.HistoryRewindRequest(
                        event_id=target.event_id, mode=rpc.HistoryRewindMode(mode)
                    ),
                    timeout=_FILES_REWIND_TIMEOUT_SECONDS if files else _REWIND_TIMEOUT_SECONDS,
                ),
                lambda r: _value(r.outcome) == "session-busy",
            )
        except RewindError:
            raise
        except Exception as exc:
            # A timeout only stops the wait: the runtime can still finish the
            # cut. Left at that, the transcript would keep turns the model no
            # longer has, and a retry would find no boundary to return to.
            if not await _cut_landed(target):
                logger.warning("rewind failed for agent %s", agent_id, exc_info=True)
                raise RewindError(f"Rewind failed: {exc}", status_code=502) from exc
            logger.warning(
                "rewind of agent %s raised but the session was cut; finishing it",
                agent_id,
                exc_info=True,
            )
            result = _UNCONFIRMED
        outcome = _value(result.outcome)
        if outcome not in _TRUNCATED:
            status_code, message = _failure_message(outcome, result, files=files)
            raise RewindError(message, status_code=status_code)
        if outcome != "success":
            logger.warning(
                "rewind of agent %s truncated history but reported %s: %s",
                agent_id,
                outcome,
                getattr(result, "error", None),
            )
        removed, boundary_at = await _truncate_archive(
            manager, agent_id, target.run_ids, target.event_id
        )
        manager._transcript.bump_epoch(agent_id)
        await _reset_run_state(manager, agent_id, run.id, target.run_ids, boundary_at)

    await manager._publish(agent_id, agent_run_id=run.id)
    return AgentRewindResult(
        events_removed=removed,
        sdk_events_removed=getattr(result, "events_removed", None),
        outcome=str(outcome),
        restored_files=list(getattr(result, "restored_files", None) or []),
        skipped_files=[
            AgentRewindSkippedFile(path=f.path, reason=str(_value(f.reason)))
            for f in getattr(result, "skipped_files", None) or []
        ],
    )


# The SDK call raised, yet the boundary is gone from the session: the cut
# landed but its report (restored files, counts) was lost.
_UNCONFIRMED = SimpleNamespace(
    outcome="unconfirmed", events_removed=None, restored_files=[], skipped_files=[], error=None
)


async def _cut_landed(target: _Target) -> bool:
    """Whether the SDK session no longer holds the rewind's boundary prompt.

    Only asked after a failed call, under the event lock: :func:`_prepare` saw
    the prompt listed moments ago, so its absence now means this rewind cut it.
    """
    try:
        listing = await target.history.list_rewind_points()
    except Exception:
        return False
    if listing.unavailable_reason is not None:
        return False
    return target.event_id not in {p.event_id for p in listing.points}


def _cut_index(events: list[AgentEvent], run_ids: Collection[int], event_id: str) -> int | None:
    for i, ev in enumerate(events):
        if ev.event_id == event_id and ev.agent_run_id in run_ids:
            return i
    return None


async def _truncate_archive(
    manager: AgentManager, agent_id: int, run_ids: frozenset[int], event_id: str
) -> tuple[int, datetime | None]:
    """Drop the session's events from the prompt ``event_id`` on, in memory and on disk.

    Another session's events past the boundary are kept: a concurrent execution
    of the same agent (a workflow step) has its own SDK session, which this
    rewind didn't touch.
    """
    events = manager._events.get(agent_id, [])
    cut = _cut_index(events, run_ids, event_id)
    if cut is None:
        return 0, None
    boundary_at = events[cut].at
    kept = events[:cut] + [ev for ev in events[cut:] if ev.agent_run_id not in run_ids]
    manager._events[agent_id] = kept
    async with SessionLocal() as session:
        # ``event_id`` was normalised to a UUID above, so it carries no LIKE
        # wildcards; the payload is compact JSON written by ``model_dump_json``.
        boundary = await session.scalar(
            select(func.min(AgentEventRecord.id)).where(
                AgentEventRecord.agent_session_id == agent_id,
                AgentEventRecord.agent_run_id.in_(run_ids),
                AgentEventRecord.payload.like(f'%"event_id":"{event_id}"%'),
            )
        )
        if boundary is not None:
            await session.execute(
                delete(AgentEventRecord).where(
                    AgentEventRecord.agent_session_id == agent_id,
                    AgentEventRecord.agent_run_id.in_(run_ids),
                    AgentEventRecord.id >= boundary,
                )
            )
            await session.commit()
    return len(events) - len(kept), boundary_at


async def _reset_run_state(
    manager: AgentManager,
    agent_id: int,
    run_id: int,
    run_ids: frozenset[int],
    boundary_at: datetime | None,
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
            if ev.agent_run_id in run_ids and ev.kind == "assistant_message" and ev.text
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
                AgentArtifact.agent_run_id.in_(run_ids),
                AgentArtifact.created_at >= boundary_at,
            )
        )
        await session.commit()
