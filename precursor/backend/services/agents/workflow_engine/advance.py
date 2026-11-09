"""Starting a workflow and advancing it when a step's agent comes to rest."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from precursor.backend.models.agent_run import AgentRun
from precursor.backend.models.agent_session import AgentSession
from precursor.backend.models.workflow import (
    Workflow,
    WorkflowRun,
    WorkflowStep,
)
from precursor.backend.services.definitions import anchors as definition_anchors
from precursor.backend.services.definitions import overlay as definition_overlay

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from precursor.backend.services.agents.manager import AgentManager

from precursor.backend.services.agents.workflow_engine.common import (
    STEP_AWAITING_PERMISSION_STATUSES,
    STEP_BLOCKED_STATUSES,
    STEP_CANCELLED_STATUSES,
    STEP_DONE_STATUSES,
    STEP_FAILED_STATUSES,
    _first_runnable,
    _is_runnable,
    _last_assistant_message,
    _load_workflow,
    _ordered_steps,
    _prev_runnable_idx,
    _publish,
    _step_label,
    _workflow_lock,
)
from precursor.backend.services.agents.workflow_engine.output import (
    _DROPPED_TURN_REASON,
    _parse_verdict,
    _step_output,
    _tidy_gate_result,
    _turn_dropped,
    _verdict_reason,
)
from precursor.backend.services.agents.workflow_engine.replay import finalize_replays_for_run
from precursor.backend.services.agents.workflow_engine.steps import (
    _advance_from,
    _apply_failure_policy,
    _begin_run,
    _enter_step,
    _finalize_run,
    _finalize_run_step,
    _open_run_step,
    _started_after,
)

logger = logging.getLogger(__name__)


async def start_workflow(
    session: AsyncSession,
    manager: AgentManager,
    workflow_id: int,
    *,
    trigger: str = "manual",
    run_input: str | None = None,
) -> Workflow | None:
    """Kick off a run: reset run state and start the first step's agent.

    ``run_input`` is an optional per-run brief (the file to analyse, the topic to
    research, a webhook payload). It's stored on the run and prepended to every
    step's kickoff preamble. Omit it and the pipeline runs autonomously on its
    steps' own objectives, exactly as before.

    Returns the updated workflow, or ``None`` if it has no runnable step. Safe to
    call on an idle/completed/draft workflow; refuses if already running.

    In files mode the step rows are first brought in line with the workflow's
    definition file; a file with errors raises :class:`DefinitionFileError`
    instead of running the stale database copy.
    """
    synced = await definition_anchors.sync_workflow(session, workflow_id)
    if synced.error is not None:
        raise definition_anchors.DefinitionFileError(synced.error)
    workflow = await _load_workflow(session, workflow_id, fresh=synced.changed)
    if workflow is None:
        return None
    if workflow.status == "running":
        return workflow
    # Files mode: the run will execute exactly these bytes of its file, so they
    # are what gets reviewed — not a scan that may be a moment older.
    snapshot = definition_overlay.read_workflow_file(workflow)
    if (
        snapshot is None
        and definition_overlay.files_mode()
        and definition_overlay.linked_file(workflow) is not None
    ):
        # Deleted, moved or broken since the last scan: nothing to pin, and the
        # database copy is no substitute.
        raise definition_anchors.DefinitionFileError(
            "its definition file couldn't be read just now; check it and start again"
        )
    if snapshot is not None and not definition_anchors.same_steps(workflow, snapshot[3]):
        raise definition_anchors.DefinitionFileError(
            f"{snapshot[0]} changed while the run was starting; start it again"
        )
    held = await definition_anchors.review_blockers(
        session, workflow, snapshot[3] if snapshot is not None else None
    )
    if held is not None:
        raise definition_anchors.DefinitionFileError(held)

    steps = _ordered_steps(workflow)
    first = _first_runnable(steps)
    if first is None:
        # Nothing to run — leave as draft.
        workflow.status = "draft"
        await session.commit()
        await _publish(workflow)
        return workflow

    now = datetime.now(UTC)
    workflow.status = "running"
    workflow.current_step_id = first.id
    workflow.run_count = (workflow.run_count or 0) + 1
    workflow.last_run_at = now
    workflow.finished_at = None
    workflow.error = None
    workflow.result_summary = None

    # Reset per-run counters so gate loop caps and retry budgets apply per run,
    # not cumulatively across the workflow's lifetime.
    for step in steps:
        step.attempt_count = 0
        step.retry_count = 0

    # Runs start clean by construction: each step opens a fresh ``AgentRun`` and
    # artifacts are scoped to it, so nothing needs pre-clearing. Wiping the
    # agent-wide rows here would destroy a concurrent workflow's blackboard —
    # exactly the bleed the run split exists to stop. ``clear_artifacts`` keeps
    # its meaning at the *read* side instead (see ``_build_context``): set, a
    # step sees only this run's board; unset, it sees every run's.

    await _begin_run(session, workflow, trigger, run_input, snapshot=snapshot)
    await session.commit()
    await _publish(workflow)

    first_idx = next((i for i, s in enumerate(steps) if s.id == first.id), 0)
    await _enter_step(session, manager, workflow, steps, first_idx)
    return workflow


async def advance_for_run(session: AsyncSession, manager: AgentManager, agent_run_id: int) -> None:
    """Completion-seam hook: advance the workflow this agent *run* belongs to.

    Called (via the manager) after a run's status commit reaches a resting or
    terminal state. A run belongs to exactly one workflow run, so this resolves
    a single workflow rather than fanning out across every workflow that happens
    to share the agent — the fan-out was the reason one agent going idle could
    advance two unrelated pipelines at once.

    Runs under the per-workflow advance lock, and everything it decides on is
    re-read **inside** that lock. The seam is fire-and-forget, so a second
    advance for the same run is routinely already queued behind this one; by the
    time it acquires the lock the run has moved on, the match below fails, and it
    returns having done nothing — instead of re-entering the same step.

    ``entered_at`` carries the moment this advance was asked for, which settles
    the case the moved-on cursor can't: a step re-entered *in place* (an
    ``on_error=retry`` loop-back) leaves the cursor where it was and opens a new
    trace, which a duplicate would otherwise happily consume. An advance is only
    ever a response to a turn that had already started, so it may not act on an
    attempt that began after it did.
    """
    entered_at = datetime.now(UTC)
    agent_run = await session.get(AgentRun, agent_run_id)
    if agent_run is None:
        return
    # Read the run's identity out now: ``expire_all`` below invalidates the
    # instance, and a lazy re-read of an expired attribute is IO in a place
    # SQLAlchemy's async layer can't await.
    agent_id = agent_run.agent_id
    launched_for_run = agent_run.workflow_run_id

    # A manual replay runs outside the pipeline, so no advance will ever close
    # its trace. Handled here because this is the one seam every ended turn
    # reaches, and it is independent of the workflow match below (a replay only
    # happens when no run is active, so that match is empty by definition).
    # Isolated behind a rollback: a half-written replay close must not leave a
    # dirty session for the advance that follows it.
    try:
        await finalize_replays_for_run(session, agent_run_id)
    except Exception:
        logger.debug("failed to close replay traces for run %s", agent_run_id, exc_info=True)
        await session.rollback()

    # A run knows the workflow run it was launched for, so there is exactly one
    # candidate. A run with no ``workflow_run_id`` (manual, scheduled, fleet) has
    # nothing to advance.
    if launched_for_run is None:
        return
    wf_run = await session.get(WorkflowRun, launched_for_run)
    if wf_run is None:
        return
    workflow_id = wf_run.workflow_id

    async with _workflow_lock(workflow_id):
        # Re-read under the lock. ``expire_all`` drops anything this session
        # cached before waiting, so a workflow another advance just moved on
        # isn't re-advanced off a stale copy.
        session.expire_all()
        workflow = await _load_workflow(session, workflow_id)
        if workflow is None or workflow.status != "running":
            return
        # Only the run that is *currently* driving the workflow may advance it: a
        # superseded attempt finishing late must not move the cursor.
        if workflow.current_run_id != launched_for_run:
            return
        current = next(
            (s for s in _ordered_steps(workflow) if s.id == workflow.current_step_id), None
        )
        if current is None or current.agent_id != agent_id:
            # The run has already been advanced past this agent's turn.
            return
        fresh = await session.get(AgentRun, agent_run_id)
        status = fresh.status if fresh else "failed"
        await _advance_one(
            session,
            manager,
            workflow,
            agent_id,
            status,
            entered_at=entered_at,
            agent_run_id=agent_run_id,
        )
        # Every terminal path in ``_advance_one`` commits, but flush anything
        # a future one might leave pending: the next advance waiting on this
        # lock must not re-read a step entry that hasn't landed yet.
        await session.commit()


async def _consume_trace(
    session: AsyncSession,
    workflow: Workflow,
    current: WorkflowStep,
    entered_at: datetime | None,
) -> bool:
    """Claim the current step's open trace; ``False`` means another advance has.

    This step's open trace is the turn's idempotency token: whoever handles the
    turn closes it, so a duplicate advance finds nothing open and stops here.
    ``entered_at`` covers the one case that leaves a *fresh* row in its place —
    a step re-entered in place by ``on_error=retry``, which reopens a trace at
    the same position before the duplicate gets to look. An advance always
    answers a turn that was already under way, so a row that started after the
    advance did belongs to the next attempt, not this one. A run-less workflow
    has no trace to consume and is exempt.
    """
    run_id = workflow.current_run_id
    if run_id is None or current.agent_id is None:
        return True
    open_trace = await _open_run_step(session, run_id, current.position, current.agent_id)
    if open_trace is None:
        return False
    return not (entered_at is not None and _started_after(open_trace, entered_at))


async def _divert(
    session: AsyncSession,
    manager: AgentManager,
    workflow: Workflow,
    steps: list[WorkflowStep],
    current_idx: int,
    agent_id: int,
    agent_status: str,
    agent_run: AgentRun | None,
) -> bool:
    """Handle a status that does not complete the step; ``True`` if it did."""
    if agent_status in STEP_FAILED_STATUSES:
        if agent_run is not None and agent_run.error:
            reason = agent_run.error
        else:
            agent = await session.get(AgentSession, agent_id)
            reason = agent.error if agent and agent.error else "A step failed."
        await _apply_failure_policy(
            session, manager, workflow, steps, current_idx, agent_id, reason
        )
        return True

    if agent_status in STEP_CANCELLED_STATUSES:
        workflow.status = "cancelled"
        workflow.finished_at = datetime.now(UTC)
        workflow.current_step_id = None
        await _finalize_run_step(session, workflow, agent_id, status="cancelled")
        await _finalize_run(session, workflow, status="cancelled")
        await session.commit()
        await _publish(workflow)
        return True

    if agent_status in STEP_AWAITING_PERMISSION_STATUSES:
        # A tool-permission gate inside a live turn. The agent picks up where it
        # left off as soon as the decision lands, so there is nothing to resume
        # and nothing to record: leave the run ``running`` with its step trace
        # open and just publish, which surfaces the approve/deny card on the
        # board. The step timeout still applies, so a gate nobody ever answers is
        # caught by the watchdog rather than parking the pipeline forever.
        await _publish(workflow)
        return True

    if agent_status in STEP_BLOCKED_STATUSES:
        # The agent raised a question and ended its turn. Hold the workflow
        # paused on this step so a resume re-drives it once answered.
        workflow.status = "paused"
        await _finalize_run_step(session, workflow, agent_id, status="blocked")
        await _finalize_run(session, workflow, status="paused")
        await session.commit()
        await _publish(workflow)
        return True

    # running / interrupted / pending — not a decision point.
    return agent_status not in STEP_DONE_STATUSES


async def _gate_verdict_text(
    session: AsyncSession, agent: AgentSession | None, agent_id: int, agent_run: AgentRun | None
) -> str:
    """The gate's raw final message, else its stored summary.

    Read the raw archived turn first: the manager cleans directive tokens out of
    ``result_summary`` for display, so the raw message is the reliable source of
    the PASS/FAIL verdict. Fall back to the summary if unavailable.
    """
    verdict_text = (
        await _last_assistant_message(
            session, agent_id, agent_run.id if agent_run is not None else None
        )
        or ""
    )
    if verdict_text:
        return verdict_text
    fallback = (
        agent_run.result_summary
        if agent_run is not None and agent_run.result_summary
        else (agent.result_summary if agent and agent.result_summary else "")
    )
    return fallback or ""


async def _fail_gate_run(
    session: AsyncSession, workflow: Workflow, agent_id: int, verdict_text: str, error: str
) -> None:
    """End the run on a gate that cannot loop back any more."""
    workflow.status = "failed"
    workflow.finished_at = datetime.now(UTC)
    workflow.current_step_id = None
    workflow.error = error[:2000]
    await _finalize_run_step(
        session,
        workflow,
        agent_id,
        status="failed",
        output_summary=_verdict_reason(verdict_text),
        gate_verdict="FAIL",
    )
    await _finalize_run(session, workflow, status="failed", error=workflow.error)
    await session.commit()
    await _publish(workflow)


async def _loop_back(
    session: AsyncSession,
    manager: AgentManager,
    workflow: Workflow,
    steps: list[WorkflowStep],
    current_idx: int,
    agent_id: int,
    verdict_text: str,
) -> None:
    """Re-enter an earlier step after a gate's FAIL, or fail the run."""
    current = steps[current_idx]
    # Where to retry: explicit on_fail_position, else the previous step.
    target_idx = current.on_fail_position
    if (
        target_idx is None
        or target_idx < 0
        or target_idx >= len(steps)
        or not _is_runnable(steps[target_idx])
    ):
        target_idx = _prev_runnable_idx(steps, current_idx)
    if target_idx is None:
        # Nothing to loop back to — a gate with no upstream is a hard fail.
        await _fail_gate_run(
            session,
            workflow,
            agent_id,
            verdict_text,
            f"Gate '{_step_label(current)}' failed and has no earlier step to retry: "
            f"{_verdict_reason(verdict_text)}",
        )
        return

    # Count this loop-back on the gate; give up once the cap is exceeded.
    current.attempt_count = (current.attempt_count or 0) + 1
    if current.attempt_count > (workflow.max_loops or 3):
        await _fail_gate_run(
            session,
            workflow,
            agent_id,
            verdict_text,
            f"Gate '{_step_label(current)}' still failing after "
            f"{workflow.max_loops} attempts: {_verdict_reason(verdict_text)}",
        )
        return

    await _finalize_run_step(
        session,
        workflow,
        agent_id,
        status="failed",
        output_summary=_verdict_reason(verdict_text),
        gate_verdict="FAIL",
    )
    await _enter_step(
        session,
        manager,
        workflow,
        steps,
        target_idx,
        fail_reason=_verdict_reason(verdict_text),
    )


async def _close_gate(
    session: AsyncSession,
    manager: AgentManager,
    workflow: Workflow,
    steps: list[WorkflowStep],
    current_idx: int,
    agent_id: int,
    agent_run: AgentRun | None,
) -> bool:
    """Parse a gate's PASS/FAIL and loop back on FAIL; ``True`` to move on."""
    agent = await session.get(AgentSession, agent_id)
    verdict_text = await _gate_verdict_text(session, agent, agent_id, agent_run)
    passed = _parse_verdict(verdict_text)
    if _turn_dropped(agent_run, verdict_text):
        # Checked before the tidy below, which would otherwise stamp a
        # fabricated "Passed" onto a turn that never spoke. A dropped gate is
        # worse than a dropped task: the verdict grammar is deliberately
        # fail-*open*, so silence waves the deliverable through. Route it
        # through the step's own error policy, where a transient fault belongs.
        await _apply_failure_policy(
            session, manager, workflow, steps, current_idx, agent_id, _DROPPED_TURN_REASON
        )
        return False
    # Normalise the gate's stored result to a plain verdict and drop its
    # auto-captured artifact — a gate judges, it doesn't publish deliverables.
    await _tidy_gate_result(session, agent, agent_run, verdict_text, passed)
    if not passed:
        await _loop_back(session, manager, workflow, steps, current_idx, agent_id, verdict_text)
        return False
    # PASS → close the gate trace, then fall through to the forward advance.
    await _finalize_run_step(
        session,
        workflow,
        agent_id,
        status="passed",
        output_summary=_verdict_reason(verdict_text),
        gate_verdict="PASS",
    )
    return True


async def _advance_one(
    session: AsyncSession,
    manager: AgentManager,
    workflow: Workflow,
    agent_id: int,
    agent_status: str,
    *,
    entered_at: datetime | None = None,
    agent_run_id: int | None = None,
) -> None:
    steps = _ordered_steps(workflow)
    # Locate the current step by id, then its index.
    current_idx = next((i for i, s in enumerate(steps) if s.id == workflow.current_step_id), None)
    if current_idx is None:
        return
    current = steps[current_idx]
    if not await _consume_trace(session, workflow, current, entered_at):
        return

    # The execution's own state, never the agent's mirror: a shared agent's row
    # may already be reporting whatever a concurrent workflow is doing to it.
    agent_run = await session.get(AgentRun, agent_run_id) if agent_run_id else None
    if await _divert(
        session, manager, workflow, steps, current_idx, agent_id, agent_status, agent_run
    ):
        return

    if current.kind == "gate":
        # A just-passed gate closes its own trace.
        if not await _close_gate(
            session, manager, workflow, steps, current_idx, agent_id, agent_run
        ):
            return
    else:
        # Step done: close its trace, then move on (or finish).
        output = await _step_output(session, agent_id, agent_run)
        if _turn_dropped(agent_run, output):
            await _apply_failure_policy(
                session, manager, workflow, steps, current_idx, agent_id, _DROPPED_TURN_REASON
            )
            return
        await _finalize_run_step(
            session,
            workflow,
            agent_id,
            status="completed",
            output_summary=output,
        )
    await _advance_from(
        session,
        manager,
        workflow,
        steps,
        current_idx,
        last_agent_id=agent_id,
        last_agent_run=agent_run,
    )
