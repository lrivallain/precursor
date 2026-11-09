"""The run-step lifecycle: opening a workflow run, entering a step (launching its
agent or opening an approval), closing and finalising trace rows, and moving
on or applying the failure policy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from precursor.backend.models.agent_run import AgentRun
from precursor.backend.models.agent_session import AgentSession
from precursor.backend.models.workflow import (
    WORKFLOW_RUN_TRIGGERS,
    Workflow,
    WorkflowRun,
    WorkflowRunStep,
    WorkflowStep,
)
from precursor.backend.services.definitions import overlay as definition_overlay

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from precursor.backend.schemas.definitions import WorkflowDefinition
    from precursor.backend.services.agents.manager import AgentManager

from precursor.backend.services.agents.workflow_engine.common import (
    _earlier_content_agent_ids,
    _next_runnable_idx,
    _prev_content_idx,
    _publish,
    _step_label,
)
from precursor.backend.services.agents.workflow_engine.context import (
    _approval_notes,
    _build_context,
    _last_run_summary,
    _run_input,
)
from precursor.backend.services.agents.workflow_engine.output import _cap_output_summary


async def _begin_run(
    session: AsyncSession,
    workflow: Workflow,
    trigger: str,
    run_input: str | None = None,
    *,
    snapshot: tuple[str, str, str, WorkflowDefinition] | None = None,
) -> None:
    """Open a fresh run-trace and point the workflow at it.

    ``snapshot`` (files mode) is the workflow file the run executes: stored with
    the run and pinned to it, so neither a later edit nor a restart changes what
    this run runs.
    """
    run = WorkflowRun(
        workflow_id=workflow.id,
        run_number=workflow.run_count or 1,
        status="running",
        trigger=trigger if trigger in WORKFLOW_RUN_TRIGGERS else "manual",
        started_at=datetime.now(UTC),
        input=(run_input.strip()[:8000] or None) if run_input else None,
    )
    if snapshot is not None:
        run.definition_path, run.definition_hash, run.definition_snapshot, _ = snapshot
    session.add(run)
    await session.flush()
    workflow.current_run_id = run.id
    if snapshot is not None and workflow.export_id:
        path, digest, _text, defn = snapshot
        definition_overlay.pin_run(workflow.export_id, run.id, defn, path, digest)
    else:
        # No version to pin: don't let a previous run's pin serve this one.
        definition_overlay.release_pin(workflow.export_id)


async def _open_run_step(
    session: AsyncSession, run_id: int, position: int, agent_id: int
) -> WorkflowRunStep | None:
    """The still-open trace for one step attempt, if there is one.

    A step entry opens exactly one of these and the advance that handles its
    turn closes it, which makes it the natural idempotency token: an advance
    that finds none has already been beaten to this turn by another. A manual
    replay's trace is excluded — it is not a turn the run is waiting on, so
    consuming it here would let a replay swallow the pipeline's own advance.
    """
    result = await session.execute(
        select(WorkflowRunStep)
        .where(
            WorkflowRunStep.run_id == run_id,
            WorkflowRunStep.position == position,
            WorkflowRunStep.agent_id == agent_id,
            WorkflowRunStep.replay.is_(False),
            WorkflowRunStep.finished_at.is_(None),
        )
        .order_by(WorkflowRunStep.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _started_after(run_step: WorkflowRunStep, moment: datetime) -> bool:
    """Did this attempt begin after ``moment``? (SQLite hands back naive times.)"""
    started = run_step.started_at
    if started is None:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return started > moment


async def _supersede_open_run_steps(session: AsyncSession, run_id: int, position: int) -> None:
    """Close any trace left open at ``position`` before a new attempt opens one.

    Should never fire now that advances are serialised, but a database written
    by an older build (or a process killed mid-step) carries rows stuck at
    ``running`` with no ``finished_at``. Left alone they render as a step that is
    forever in flight, and they'd sit in front of a manual retry's own trace.
    """
    rows = (
        (
            await session.execute(
                select(WorkflowRunStep).where(
                    WorkflowRunStep.run_id == run_id,
                    WorkflowRunStep.position == position,
                    WorkflowRunStep.finished_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.status = "superseded"
        row.finished_at = row.started_at or datetime.now(UTC)


async def _snapshot_step_overrides(run: AgentRun, workflow: Workflow, step: WorkflowStep) -> None:
    """Bake the step's (and workflow's) overrides into the run's own snapshot.

    Agents are shared, reusable rows, so a step's capability choices and the
    workflow's Assistant Role belong to *this execution*, not to the definition.
    Writing them onto the run means two workflows can drive the same agent with
    different toggles at the same time, and an edit to the definition mid-run
    can't change what an in-flight step is executing with. A ``None`` override
    leaves the run's inherited setting alone — except for the MCP server scope,
    which is always assigned (see below).
    """
    if step.use_mcp is not None:
        run.use_mcp = step.use_mcp
    if step.use_skills is not None:
        run.use_skills = step.use_skills
    if step.use_memory is not None:
        run.use_memory = step.use_memory
    # Unlike the toggles above, the server scope is assigned even when null:
    # null means "the whole enabled catalogue", not "whatever the definition
    # happens to carry". Without this a narrow step would silently inherit a
    # scope it never asked for.
    run.mcp_servers = step.mcp_servers
    if workflow.role is not None:
        run.role = workflow.role
    if workflow.approval_policy is not None:
        # The pipeline's tool-approval stance wins for the duration of the run.
        # On the run rather than the agent, so a shared agent keeps its own
        # policy everywhere else it is used — including in a concurrent workflow.
        run.approval_policy = workflow.approval_policy


async def _open_agent_run(
    session: AsyncSession,
    agent_id: int,
    workflow: Workflow,
    step: WorkflowStep | None,
    *,
    trigger: str = "workflow",
    workflow_run_id: int | None = None,
) -> AgentRun | None:
    """Open the :class:`AgentRun` this step will execute as.

    Created here rather than in the manager because the run id has to land on the
    ``WorkflowRunStep`` trace in the same transaction — the trace is the link
    between "attempt 2 of step 3" and "the execution that produced it".

    ``step`` is optional so a replay can still open a run when the definition has
    been edited out from under it; the agent's current settings then stand in for
    the vanished step's overrides.
    """
    agent = await session.get(AgentSession, agent_id)
    if agent is None:
        return None
    run = AgentRun(
        agent_id=agent.id,
        trigger=trigger,
        workflow_run_id=workflow_run_id,
        status="pending",
        model=agent.model,
        use_mcp=agent.use_mcp,
        use_skills=agent.use_skills,
        use_memory=agent.use_memory,
        mcp_servers=agent.mcp_servers,
        approval_policy=agent.approval_policy,
        role=agent.role,
        started_at=datetime.now(UTC),
    )
    if step is not None:
        await _snapshot_step_overrides(run, workflow, step)
    if (source := definition_overlay.provenance(agent)) is not None:
        run.definition_path, run.definition_hash = source
    session.add(run)
    await session.flush()
    agent.current_run_id = run.id
    return run


async def _launch_step(
    session: AsyncSession,
    manager: AgentManager,
    workflow: Workflow,
    step: WorkflowStep,
    context: str | None,
) -> None:
    """Open the step's agent run, record its trace, then enqueue it.

    The single choke point through which every step is started, so every attempt
    — including gate loop-backs — leaves a trace with the exact input it saw and
    the exact execution that ran it.
    """
    agent_id = step.agent_id
    if agent_id is None:
        # ``_enter_step`` never routes an agent-less step here; be explicit.
        return

    run_id = workflow.current_run_id
    agent_run = await _open_agent_run(session, agent_id, workflow, step, workflow_run_id=run_id)
    if agent_run is None:
        return

    if run_id is not None:
        await _supersede_open_run_steps(session, run_id, step.position)
        prior = await session.execute(
            select(func.count(WorkflowRunStep.id)).where(
                WorkflowRunStep.run_id == run_id,
                WorkflowRunStep.position == step.position,
                # Manual replays are numbered on their own sequence, so a replay
                # never inflates "the pipeline drove this step N times".
                WorkflowRunStep.replay.is_(False),
            )
        )
        attempt = int(prior.scalar() or 0) + 1
        run_step = WorkflowRunStep(
            run_id=run_id,
            position=step.position,
            kind=step.kind,
            label=_step_label(step),
            agent_id=agent_id,
            agent_run_id=agent_run.id,
            attempt=attempt,
            status="running",
            input_context=context,
            started_at=datetime.now(UTC),
            # The run starts at zero, so its own counters *are* the attempt's
            # spend — no baseline arithmetic against a shared cumulative total,
            # and no cross-attribution when two workflows share an agent.
            token_baseline_in=0,
            token_baseline_out=0,
        )
        session.add(run_step)
        await session.flush()
        agent_run.workflow_run_step_id = run_step.id
    await session.commit()
    manager.enqueue(manager.start_task(agent_id, extra_context=context, run_id=agent_run.id))


async def _close_run_step(
    session: AsyncSession,
    run_step: WorkflowRunStep,
    *,
    status: str,
    output_summary: str | None = None,
    gate_verdict: str | None = None,
) -> None:
    """Stamp one trace row with its outcome, spend, and finish time.

    Shared by the pipeline's advance and by a manual replay, so a replayed
    attempt is accounted for exactly like the turn it re-runs.
    """
    run_step.status = status
    run_step.finished_at = datetime.now(UTC)
    if output_summary is not None:
        run_step.output_summary = _cap_output_summary(output_summary)
    if gate_verdict is not None:
        run_step.gate_verdict = gate_verdict

    agent = await session.get(AgentSession, run_step.agent_id) if run_step.agent_id else None
    agent_run = (
        await session.get(AgentRun, run_step.agent_run_id) if run_step.agent_run_id else None
    )

    # Keep the agent's Agents-section unread badge clear for turns it ran *as a
    # workflow step*: the workflow is the coordinator here, so these replies
    # aren't the "autonomous agent finished something for you" signal the badge
    # is meant to convey. Advancing ``last_read_at`` past this step's events
    # marks them read without touching genuinely autonomous (manual/scheduled)
    # runs, whose events land after their own ``last_read_at``.
    if agent is not None:
        agent.last_read_at = run_step.finished_at

    # Cost accounting: this attempt's spend is its *own* run's counters. Baselines
    # are kept for rows written before runs existed (and stay at zero for new
    # ones), so a legacy trace still subtracts correctly. Clamped at zero because
    # a run whose context was cleared mid-flight can see its totals reset.
    if agent_run is not None:
        run_step.input_tokens = max(
            0, (agent_run.total_input_tokens or 0) - run_step.token_baseline_in
        )
        run_step.output_tokens = max(
            0, (agent_run.total_output_tokens or 0) - run_step.token_baseline_out
        )
    elif agent is not None:
        run_step.input_tokens = max(0, agent.total_input_tokens - run_step.token_baseline_in)
        run_step.output_tokens = max(0, agent.total_output_tokens - run_step.token_baseline_out)
    if agent_run is not None or agent is not None:
        run = await session.get(WorkflowRun, run_step.run_id)
        if run is not None:
            run.total_input_tokens = (run.total_input_tokens or 0) + run_step.input_tokens
            run.total_output_tokens = (run.total_output_tokens or 0) + run_step.output_tokens


async def _finalize_run_step(
    session: AsyncSession,
    workflow: Workflow,
    agent_id: int,
    *,
    status: str,
    output_summary: str | None = None,
    gate_verdict: str | None = None,
) -> None:
    """Close out the still-open trace for this agent in the current run."""
    run_id = workflow.current_run_id
    if run_id is None:
        return
    result = await session.execute(
        select(WorkflowRunStep)
        .where(
            WorkflowRunStep.run_id == run_id,
            WorkflowRunStep.agent_id == agent_id,
            # A replay's trace is closed by the replay seam, never by an advance.
            WorkflowRunStep.replay.is_(False),
            WorkflowRunStep.finished_at.is_(None),
        )
        .order_by(WorkflowRunStep.id.desc())
        .limit(1)
    )
    run_step = result.scalar_one_or_none()
    if run_step is None:
        return
    await _close_run_step(
        session,
        run_step,
        status=status,
        output_summary=output_summary,
        gate_verdict=gate_verdict,
    )


async def _finalize_run(
    session: AsyncSession,
    workflow: Workflow,
    *,
    status: str,
    result_summary: str | None = None,
    error: str | None = None,
) -> None:
    """Stamp the run row with its terminal (or paused) outcome."""
    run_id = workflow.current_run_id
    if run_id is None:
        return
    run = await session.get(WorkflowRun, run_id)
    if run is None:
        return
    run.status = status
    if status in ("completed", "failed", "cancelled"):
        run.finished_at = datetime.now(UTC)
        # The run is over: its pinned file version goes (a retry restores it
        # from the run's snapshot), and steps read the file as it is again.
        definition_overlay.release_pin(workflow.export_id, run_id)
    else:
        # Re-opened (resumed or retried): a run that is going again hasn't
        # finished, and must not keep a stale outcome from the attempt that
        # stopped it.
        run.finished_at = None
        run.error = None
    if result_summary is not None:
        run.result_summary = result_summary[:2000]
    if error is not None:
        run.error = error[:2000]


# --- Step entry / advancement ------------------------------------------------
# Every path that moves a run onto a step funnels through ``_enter_step`` — the
# first step of a run, a forward advance, a gate loop-back, a failure retry, an
# approval resume. That's what lets an ``approval`` step (which has no agent and
# must park for a human) work identically wherever it sits in the pipeline.


async def _open_approval_trace(
    session: AsyncSession, workflow: Workflow, step: WorkflowStep
) -> None:
    """Record the trace row for an approval checkpoint the run is parking on."""
    run_id = workflow.current_run_id
    if run_id is None:
        return
    prior = await session.execute(
        select(func.count(WorkflowRunStep.id)).where(
            WorkflowRunStep.run_id == run_id,
            WorkflowRunStep.position == step.position,
        )
    )
    session.add(
        WorkflowRunStep(
            run_id=run_id,
            position=step.position,
            kind="approval",
            label=_step_label(step),
            agent_id=None,
            attempt=int(prior.scalar() or 0) + 1,
            status="awaiting_approval",
            input_context=step.instructions,
            started_at=datetime.now(UTC),
        )
    )


async def _finalize_step_by_position(
    session: AsyncSession,
    workflow: Workflow,
    position: int,
    *,
    status: str,
    output_summary: str | None = None,
) -> None:
    """Close an open trace addressed by *position* rather than agent.

    Approval traces carry no ``agent_id``, so ``_finalize_run_step`` (which looks
    a trace up by its agent) can't reach them.
    """
    run_id = workflow.current_run_id
    if run_id is None:
        return
    result = await session.execute(
        select(WorkflowRunStep)
        .where(
            WorkflowRunStep.run_id == run_id,
            WorkflowRunStep.position == position,
            WorkflowRunStep.replay.is_(False),
            WorkflowRunStep.finished_at.is_(None),
        )
        .order_by(WorkflowRunStep.id.desc())
        .limit(1)
    )
    run_step = result.scalar_one_or_none()
    if run_step is None:
        return
    run_step.status = status
    run_step.finished_at = datetime.now(UTC)
    if output_summary is not None:
        run_step.output_summary = _cap_output_summary(output_summary)


def _selected_source_positions(step: WorkflowStep, max_position: int) -> list[int]:
    """Parse ``context_sources`` into valid, ordered, de-duplicated positions."""
    raw = (step.context_sources or "").replace(";", ",")
    out: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk.isdigit():
            continue
        pos = int(chunk)
        if 0 <= pos <= max_position and pos not in out:
            out.append(pos)
    return sorted(out)


async def _enter_step(
    session: AsyncSession,
    manager: AgentManager,
    workflow: Workflow,
    steps: list[WorkflowStep],
    idx: int,
    *,
    fail_reason: str | None = None,
    human_feedback: str | None = None,
    unblock_guidance: str | None = None,
) -> None:
    """Drive the run into ``steps[idx]``: park for a human, or launch its agent."""
    step = steps[idx]
    workflow.current_step_id = step.id

    if step.kind == "approval":
        # Hand the wheel to the human and stop. Nothing runs until they decide.
        workflow.status = "awaiting_approval"
        await _open_approval_trace(session, workflow, step)
        await _finalize_run(session, workflow, status="awaiting_approval")
        await session.commit()
        await _publish(workflow)
        return

    if step.agent_id is None:
        return

    # What the agent parked itself on, read *before* the launch clears it, so the
    # guidance below can quote the question it answers.
    blocked_question: str | None = None
    if unblock_guidance:
        parked = await session.get(AgentSession, step.agent_id)
        blocked_question = parked.blocked_question if parked else None

    # Capability overrides are snapshotted onto the run in ``_launch_step`` below,
    # not written onto the agent — the definition is shared, the execution isn't.
    await session.commit()
    await _publish(workflow)

    # What this step inherits. ``auto`` is the implicit pipeline hand-off;
    # ``selected`` narrows it to named earlier steps; ``none`` cuts it off so the
    # step runs on its own objective (plus the run brief) alone.
    mode = step.context_mode or "auto"
    prev_agent_id: int | None = None
    earlier_ids: list[int] = []
    if mode == "selected":
        wanted = _selected_source_positions(step, max(0, len(steps) - 1))
        sources = [
            s.agent_id
            for pos in wanted
            for s in steps
            if s.position == pos and s.agent_id is not None and pos != step.position
        ]
        if sources:
            # Newest selection is the immediate hand-off; the rest form the board.
            prev_agent_id = sources[-1]
            earlier_ids = sources[:-1]
    elif mode != "none":
        prev_idx = _prev_content_idx(steps, idx)
        prev_agent_id = steps[prev_idx].agent_id if prev_idx is not None else None
        earlier_ids = _earlier_content_agent_ids(steps, prev_idx)

    context = await _build_context(
        session,
        workflow,
        step,
        prev_agent_id,
        earlier_agent_ids=earlier_ids,
        fail_reason=fail_reason,
        human_feedback=human_feedback,
        unblock_guidance=unblock_guidance,
        blocked_question=blocked_question,
        run_input=await _run_input(session, workflow),
        approval_notes=await _approval_notes(session, workflow),
        workflow_run_id=workflow.current_run_id,
    )
    await _launch_step(session, manager, workflow, step, context)


async def _complete_run(
    session: AsyncSession,
    workflow: Workflow,
    *,
    last_agent_id: int | None,
    last_agent_run: AgentRun | None = None,
) -> None:
    """Finish the pipeline: stamp the workflow and run with the final deliverable."""
    workflow.status = "completed"
    workflow.finished_at = datetime.now(UTC)
    workflow.current_step_id = None
    # The final step's *run* holds the deliverable; the agent row only mirrors
    # whichever execution touched it last, which for a shared agent may be
    # another workflow's.
    if last_agent_run is not None and last_agent_run.result_summary:
        workflow.result_summary = last_agent_run.result_summary[:2000]
    elif last_agent_id is not None:
        summary = await _last_run_summary(session, last_agent_id, workflow.current_run_id)
        if summary:
            workflow.result_summary = summary[:2000]
    await _finalize_run(
        session, workflow, status="completed", result_summary=workflow.result_summary
    )
    await session.commit()
    await _publish(workflow)


async def _advance_from(
    session: AsyncSession,
    manager: AgentManager,
    workflow: Workflow,
    steps: list[WorkflowStep],
    from_idx: int,
    *,
    last_agent_id: int | None = None,
    last_agent_run: AgentRun | None = None,
) -> None:
    """Move to the next runnable step after ``from_idx``, or finish the run."""
    next_idx = _next_runnable_idx(steps, from_idx)
    if next_idx is None:
        await _complete_run(
            session, workflow, last_agent_id=last_agent_id, last_agent_run=last_agent_run
        )
        return
    await _enter_step(session, manager, workflow, steps, next_idx)


async def _apply_failure_policy(
    session: AsyncSession,
    manager: AgentManager,
    workflow: Workflow,
    steps: list[WorkflowStep],
    idx: int,
    agent_id: int,
    reason: str,
) -> None:
    """Handle a failed (or timed-out) step according to its ``on_error`` policy.

    ``retry`` re-drives the same step with the failure reason injected, up to
    ``max_retries``. ``continue`` records the failure and carries on — for steps
    whose output is optional. ``fail`` (and an exhausted retry budget) stops the
    whole run, which is the conservative default.
    """
    step = steps[idx]
    policy = step.on_error or "fail"

    if policy == "retry" and (step.retry_count or 0) < (step.max_retries or 0):
        step.retry_count = (step.retry_count or 0) + 1
        await _finalize_run_step(
            session, workflow, agent_id, status="failed", output_summary=reason
        )
        await session.commit()
        await _publish(workflow)
        await _enter_step(
            session,
            manager,
            workflow,
            steps,
            idx,
            fail_reason=f"The previous attempt failed: {reason}",
        )
        return

    if policy == "continue":
        await _finalize_run_step(
            session, workflow, agent_id, status="failed", output_summary=reason
        )
        await session.commit()
        await _publish(workflow)
        # The failed step produced nothing usable, so hand the *previous*
        # producer's output onward rather than a dead end.
        await _advance_from(session, manager, workflow, steps, idx, last_agent_id=None)
        return

    workflow.status = "failed"
    workflow.finished_at = datetime.now(UTC)
    workflow.current_step_id = None
    workflow.error = reason[:2000]
    await _finalize_run_step(session, workflow, agent_id, status="failed", output_summary=reason)
    await _finalize_run(session, workflow, status="failed", error=workflow.error)
    await session.commit()
    await _publish(workflow)
