"""User controls on a running workflow: pause, resume, cancel, retry, approve or
reject a step, and answer a step's tool-permission request.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from precursor.backend.models.workflow import (
    WORKFLOW_STEP_REJECT_POLICIES,
    Workflow,
    WorkflowRunStep,
)
from precursor.backend.services.definitions import anchors as definition_anchors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from precursor.backend.services.agents.manager import AgentManager

from precursor.backend.services.agents.workflow_engine.common import (
    _current_run_for,
    _first_runnable,
    _is_runnable,
    _load_workflow,
    _ordered_steps,
    _prev_content_idx,
    _prev_runnable_idx,
    _publish,
    _step_label,
)
from precursor.backend.services.agents.workflow_engine.steps import (
    _advance_from,
    _enter_step,
    _finalize_run,
    _finalize_run_step,
    _finalize_step_by_position,
)


async def pause_workflow(session: AsyncSession, workflow_id: int) -> Workflow | None:
    """Hold a running workflow between steps (the active agent keeps its turn;
    advancement is suppressed while paused)."""
    workflow = await _load_workflow(session, workflow_id)
    if workflow is None:
        return None
    if workflow.status == "running":
        workflow.status = "paused"
        await session.commit()
        await _publish(workflow)
    return workflow


async def resume_workflow(
    session: AsyncSession,
    manager: AgentManager,
    workflow_id: int,
    *,
    guidance: str | None = None,
) -> Workflow | None:
    """Resume a paused workflow by re-driving its current step's agent.

    ``guidance`` is the answer to whatever parked the run. A pause is usually
    just a pause, but a run also parks when a step's agent **blocks** on a
    question — and re-driving that step unchanged would strand it on the same
    question. Supplying guidance injects the answer into the step's kickoff so
    the retry can actually get past it.
    """
    workflow = await _load_workflow(session, workflow_id)
    if workflow is None:
        return None
    if workflow.status != "paused":
        return workflow
    if await definition_anchors.guard_continuation(session, workflow):
        # Re-pinned: the rows loaded above were projected from the file as it
        # is now, not from the version this run executes.
        workflow = await _load_workflow(session, workflow_id, fresh=True) or workflow

    steps = _ordered_steps(workflow)
    current = next((s for s in steps if s.id == workflow.current_step_id), None)
    if current is None or not _is_runnable(current):
        current = _first_runnable(steps)
    if current is None:
        return workflow

    workflow.status = "running"
    # Re-open the paused run trace so the resumed attempt records under it.
    await _finalize_run(session, workflow, status="running")

    # ``_enter_step`` re-derives the step's context (and parks again if the
    # resumed step is an approval checkpoint).
    idx = next((i for i, s in enumerate(steps) if s.id == current.id), 0)
    await _enter_step(
        session,
        manager,
        workflow,
        steps,
        idx,
        unblock_guidance=(guidance.strip() if guidance and guidance.strip() else None),
    )
    return workflow


async def resolve_step_permission(
    session: AsyncSession,
    manager: AgentManager,
    workflow_id: int,
    *,
    request_id: str,
    decision: str,
) -> tuple[Workflow | None, bool]:
    """Answer the tool-permission gate parking a step, and let the run carry on.

    Resolving the gate in the runtime is only half of it. The block paused the
    *workflow* and closed the step's trace, and ``advance_for_run`` only ever
    looks at ``running`` workflows — so an approved agent would happily finish
    its turn into a pipeline that had stopped listening, leaving the board stuck
    on "Blocked" forever. Putting the run back to ``running`` and opening a trace
    for the continuing attempt restores the seam, so the coordinator advances
    when the turn lands.

    Returns ``(workflow, resolved)``. ``resolved`` is False when no live request
    matched — a stale card from a gate that has since been cancelled or answered
    elsewhere — so the caller can say so rather than silently doing nothing.
    """
    workflow = await _load_workflow(session, workflow_id)
    if workflow is None:
        return None, False

    steps = _ordered_steps(workflow)
    # The parked agent is whichever step is holding the run. Fall back to
    # scanning, because the cursor can lag a publish.
    current = next((s for s in steps if s.id == workflow.current_step_id), None)
    candidates = [current] if current and current.agent_id else [s for s in steps if s.agent_id]
    resolved = False
    agent_id: int | None = None
    for step in candidates:
        if step is None or step.agent_id is None:
            continue
        if await manager.resolve_permission(step.agent_id, request_id, decision):
            resolved = True
            agent_id = step.agent_id
            target = step
            break
    if not resolved or agent_id is None:
        return workflow, False

    if workflow.status == "paused":
        workflow.status = "running"
        workflow.current_step_id = target.id
        await _finalize_run(session, workflow, status="running")
        # The blocked attempt's trace was closed when the run parked; the turn
        # now continuing is a new one, so give it somewhere to record.
        run_id = workflow.current_run_id
        if run_id is not None:
            prior = await session.execute(
                select(func.count(WorkflowRunStep.id)).where(
                    WorkflowRunStep.run_id == run_id,
                    WorkflowRunStep.position == target.position,
                )
            )
            # The gate resumed the *same* execution, so the continuing attempt
            # baselines against that run's counters as they stand right now.
            agent_run = await _current_run_for(session, agent_id, run_id)
            session.add(
                WorkflowRunStep(
                    run_id=run_id,
                    position=target.position,
                    kind=target.kind,
                    label=_step_label(target),
                    agent_id=agent_id,
                    agent_run_id=agent_run.id if agent_run is not None else None,
                    attempt=int(prior.scalar() or 0) + 1,
                    status="running",
                    input_context=(
                        f"Permission {'granted' if decision != 'deny' else 'denied'} by a human; "
                        "the step continued from where it stopped."
                    ),
                    started_at=datetime.now(UTC),
                    token_baseline_in=(
                        (agent_run.total_input_tokens or 0) if agent_run is not None else 0
                    ),
                    token_baseline_out=(
                        (agent_run.total_output_tokens or 0) if agent_run is not None else 0
                    ),
                )
            )
        await session.commit()
        await _publish(workflow)
    return workflow, True


async def retry_step(
    session: AsyncSession,
    manager: AgentManager,
    workflow_id: int,
    *,
    position: int | None = None,
    guidance: str | None = None,
) -> Workflow | None:
    """Re-drive one step of a stopped run as a fresh attempt, in place.

    When a step fails under the ``fail`` policy the whole run stops — and the
    only way back was to run the pipeline again from step 1, throwing away every
    good step before the bad one (and paying for them twice). This picks the run
    back up at the step that broke: it re-enters that step, which appends a new
    attempt to the *same* run trace, and carries on through the rest of the
    pipeline from there.

    ``position`` targets a specific step; omitted, it retries the one that
    stopped the run. ``guidance`` is optional human input injected into the
    retry, for a failure the agent can't diagnose on its own.
    """
    workflow = await _load_workflow(session, workflow_id)
    if workflow is None:
        return None
    # Only a stopped run can be picked back up: retrying a step underneath a
    # live run would race the coordinator that is still driving it.
    if workflow.status not in ("failed", "cancelled"):
        return workflow
    if await definition_anchors.guard_continuation(session, workflow):
        # Re-pinned: the rows loaded above were projected from the file as it
        # is now, not from the version this run executes.
        workflow = await _load_workflow(session, workflow_id, fresh=True) or workflow

    steps = _ordered_steps(workflow)
    if not steps:
        return workflow

    target_idx: int | None = None
    if position is not None:
        target_idx = next((i for i, s in enumerate(steps) if s.position == position), None)
    else:
        failed_pos = await _last_failed_position(session, workflow)
        if failed_pos is not None:
            target_idx = next((i for i, s in enumerate(steps) if s.position == failed_pos), None)
    if target_idx is None:
        return workflow

    step = steps[target_idx]
    # An approval checkpoint has no agent to re-drive; re-entering it just parks
    # the run for the human again, which is the right behaviour.
    if not _is_runnable(step) and step.kind != "approval":
        return workflow

    # A retry is a fresh attempt at the same step, so the exhausted automatic
    # retry budget resets — otherwise the manual retry would inherit a spent
    # counter and give up immediately on its next failure.
    step.retry_count = 0
    workflow.status = "running"
    workflow.error = None
    workflow.finished_at = None
    await _finalize_run(session, workflow, status="running")
    await session.commit()
    await _publish(workflow)

    await _enter_step(
        session,
        manager,
        workflow,
        steps,
        target_idx,
        unblock_guidance=(guidance.strip() if guidance and guidance.strip() else None),
    )
    return workflow


async def _last_failed_position(session: AsyncSession, workflow: Workflow) -> int | None:
    """Position of the step whose failure stopped the current run.

    Read from the run trace rather than ``current_step_id``, which the failure
    path clears on its way out.
    """
    if workflow.current_run_id is None:
        return None
    result = await session.execute(
        select(WorkflowRunStep.position)
        .where(
            WorkflowRunStep.run_id == workflow.current_run_id,
            WorkflowRunStep.status.in_(("failed", "cancelled")),
            # A replay that went badly didn't stop the run; it must not redirect
            # a retry away from the step that actually did.
            WorkflowRunStep.replay.is_(False),
        )
        .order_by(WorkflowRunStep.id.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return int(row) if row is not None else None


# --- Human approval ---------------------------------------------------------
# An ``approval`` step is the counterpart to a gate: a gate is an *agent* judging
# the work, this is a *human*. The run parks on it — nothing burns tokens while
# it waits — until someone approves (carry on) or rejects with feedback (loop
# back and redo, reusing the gate's loop-back machinery and cap).


async def approve_step(
    session: AsyncSession,
    manager: AgentManager,
    workflow_id: int,
    *,
    note: str | None = None,
) -> Workflow | None:
    """Clear a human approval checkpoint and let the pipeline continue."""
    workflow = await _load_workflow(session, workflow_id)
    if workflow is None:
        return None
    if workflow.status != "awaiting_approval":
        return workflow
    if await definition_anchors.guard_continuation(session, workflow):
        # Re-pinned: the rows loaded above were projected from the file as it
        # is now, not from the version this run executes.
        workflow = await _load_workflow(session, workflow_id, fresh=True) or workflow

    steps = _ordered_steps(workflow)
    idx = next((i for i, s in enumerate(steps) if s.id == workflow.current_step_id), None)
    if idx is None:
        return workflow

    workflow.status = "running"
    await _finalize_step_by_position(
        session,
        workflow,
        steps[idx].position,
        status="passed",
        output_summary=(note.strip() if note and note.strip() else "Approved."),
    )
    await _finalize_run(session, workflow, status="running")
    await session.commit()
    await _publish(workflow)

    # An approval produces no content, so the deliverable stays whatever the last
    # real producer made — which is what a final ``_complete_run`` should report.
    content_idx = _prev_content_idx(steps, idx)
    last_agent_id = steps[content_idx].agent_id if content_idx is not None else None
    await _advance_from(session, manager, workflow, steps, idx, last_agent_id=last_agent_id)
    return workflow


async def reject_step(
    session: AsyncSession,
    manager: AgentManager,
    workflow_id: int,
    *,
    feedback: str | None = None,
    action: str | None = None,
) -> Workflow | None:
    """Send the work back — or stop the run — from a human approval checkpoint.

    What happens next comes from the step's ``on_reject`` policy, which the
    reviewer may override per decision via ``action``:

    * ``rework`` — mirror a failing gate: loop back to ``on_fail_position``
      (default: the previous producing step) with the feedback injected, bounded
      by the workflow's ``max_loops`` cap.
    * ``stop`` — end the run here. A deliberate human "no", recorded as
      ``cancelled`` rather than ``failed``: nothing broke, someone decided.
    * ``skip`` — drop the rejected work and carry on to the next step.
    """
    workflow = await _load_workflow(session, workflow_id)
    if workflow is None:
        return None
    if workflow.status != "awaiting_approval":
        return workflow
    # Anything but stopping runs more steps. (A step that *declares* stop still
    # goes through the guard, which only refuses a reshaped run.)
    if action != "stop" and await definition_anchors.guard_continuation(session, workflow):
        # Re-pinned: the rows loaded above were projected from the file as it
        # is now, not from the version this run executes.
        workflow = await _load_workflow(session, workflow_id, fresh=True) or workflow

    steps = _ordered_steps(workflow)
    idx = next((i for i, s in enumerate(steps) if s.id == workflow.current_step_id), None)
    if idx is None:
        return workflow

    current = steps[idx]
    note = (feedback or "").strip() or "A reviewer rejected this and asked for a new version."
    policy = action if action in WORKFLOW_STEP_REJECT_POLICIES else (current.on_reject or "rework")

    if policy == "stop":
        workflow.status = "cancelled"
        workflow.finished_at = datetime.now(UTC)
        workflow.current_step_id = None
        workflow.result_summary = f"Stopped at '{_step_label(current)}': {note}"[:2000]
        await _finalize_step_by_position(
            session, workflow, current.position, status="failed", output_summary=note
        )
        await _finalize_run(session, workflow, status="cancelled", result_summary=note)
        await session.commit()
        await _publish(workflow)
        return workflow

    if policy == "skip":
        workflow.status = "running"
        await _finalize_step_by_position(
            session, workflow, current.position, status="skipped", output_summary=note
        )
        await _finalize_run(session, workflow, status="running")
        await session.commit()
        await _publish(workflow)
        # The rejected work is abandoned, so the next step inherits the last real
        # producer's output rather than anything from this checkpoint.
        content_idx = _prev_content_idx(steps, idx)
        last_agent_id = steps[content_idx].agent_id if content_idx is not None else None
        await _advance_from(session, manager, workflow, steps, idx, last_agent_id=last_agent_id)
        return workflow

    target_idx = current.on_fail_position
    if (
        target_idx is None
        or target_idx < 0
        or target_idx >= len(steps)
        or not _is_runnable(steps[target_idx])
        or target_idx == idx
    ):
        target_idx = _prev_runnable_idx(steps, idx)

    if target_idx is None:
        workflow.status = "failed"
        workflow.finished_at = datetime.now(UTC)
        workflow.current_step_id = None
        workflow.error = (
            f"Rejected at '{_step_label(current)}' with no earlier step to redo: {note}"[:2000]
        )
        await _finalize_step_by_position(
            session, workflow, current.position, status="failed", output_summary=note
        )
        await _finalize_run(session, workflow, status="failed", error=workflow.error)
        await session.commit()
        await _publish(workflow)
        return workflow

    current.attempt_count = (current.attempt_count or 0) + 1
    if current.attempt_count > (workflow.max_loops or 3):
        workflow.status = "failed"
        workflow.finished_at = datetime.now(UTC)
        workflow.current_step_id = None
        workflow.error = (
            f"Rejected at '{_step_label(current)}' after {workflow.max_loops} attempts: {note}"
        )[:2000]
        await _finalize_step_by_position(
            session, workflow, current.position, status="failed", output_summary=note
        )
        await _finalize_run(session, workflow, status="failed", error=workflow.error)
        await session.commit()
        await _publish(workflow)
        return workflow

    workflow.status = "running"
    await _finalize_step_by_position(
        session, workflow, current.position, status="failed", output_summary=note
    )
    await _finalize_run(session, workflow, status="running")
    await _enter_step(session, manager, workflow, steps, target_idx, human_feedback=note)
    return workflow


async def cancel_workflow(
    session: AsyncSession, manager: AgentManager, workflow_id: int
) -> Workflow | None:
    """Stop a workflow and cancel its in-flight step agent."""
    workflow = await _load_workflow(session, workflow_id)
    if workflow is None:
        return None

    steps = _ordered_steps(workflow)
    current = next((s for s in steps if s.id == workflow.current_step_id), None)

    workflow.status = "cancelled"
    workflow.finished_at = datetime.now(UTC)
    workflow.current_step_id = None
    cancelled_run_id: int | None = None
    if current is not None and current.agent_id is not None:
        # Resolve *this* workflow's execution before the trace closes; a shared
        # agent's current run may belong to a different pipeline.
        agent_run = await _current_run_for(session, current.agent_id, workflow.current_run_id)
        cancelled_run_id = agent_run.id if agent_run is not None else None
        await _finalize_run_step(session, workflow, current.agent_id, status="cancelled")
    await _finalize_run(session, workflow, status="cancelled")
    await session.commit()
    await _publish(workflow)

    if current is not None and current.agent_id is not None:
        manager.enqueue(manager.cancel(current.agent_id, run_id=cancelled_run_id))
    return workflow
