"""Replaying a single step outside the run's flow, closing those replays, and
reading the events of one step attempt.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from precursor.backend.models.agent_event import AgentEventRecord
from precursor.backend.models.agent_run import AgentRun
from precursor.backend.models.agent_session import AgentSession
from precursor.backend.models.workflow import (
    Workflow,
    WorkflowRun,
    WorkflowRunStep,
)
from precursor.backend.services.agents.event_normalizer import is_content_free
from precursor.backend.services.workflow_step_retention import PRUNED_PLACEHOLDER

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from precursor.backend.services.agents.manager import AgentManager

from precursor.backend.services.agents.workflow_engine.common import (
    STEP_AWAITING_PERMISSION_STATUSES,
    STEP_BLOCKED_STATUSES,
    STEP_CANCELLED_STATUSES,
    STEP_DONE_STATUSES,
    STEP_FAILED_STATUSES,
    _last_assistant_message,
    _load_workflow,
    _ordered_steps,
    _publish,
)
from precursor.backend.services.agents.workflow_engine.output import (
    _DROPPED_TURN_REASON,
    _parse_verdict,
    _step_output,
    _tidy_gate_result,
    _turn_dropped,
    _verdict_reason,
)
from precursor.backend.services.agents.workflow_engine.steps import (
    _close_run_step,
    _open_agent_run,
)

# --- Manual replay ----------------------------------------------------------
# Retrying is about *recovering a run*: it re-drives the step that stopped the
# pipeline and carries on through everything after it. Replaying is about
# *interrogating a step*: an operator reading a finished trace wants to see what
# this one step does when handed the very same input again — a different sample
# from a non-deterministic model, or a second look after tweaking the agent's
# prompt or its tools. So a replay deliberately does none of the coordination:
# it never moves ``current_step_id``, never reopens the run, and nothing advances
# when it ends. It appends one clearly-marked attempt to the run it belongs to,
# which is why it works just as well on a run that succeeded.

# An agent in one of these is mid-turn; replaying would hand it a second prompt
# on top of the one it is already answering.
_REPLAY_BUSY_STATUSES = ("running", "needs_approval", "pending")


async def replay_step(
    session: AsyncSession,
    manager: AgentManager,
    workflow_id: int,
    *,
    step_run_id: int,
) -> tuple[Workflow | None, str | None]:
    """Re-run one recorded step attempt on its own, with the input it first saw.

    Returns ``(workflow, refusal)`` — ``workflow`` is ``None`` when the attempt
    doesn't belong to this workflow (a 404), and ``refusal`` carries a
    human-readable reason when the replay can't be honoured right now.
    """
    workflow = await _load_workflow(session, workflow_id)
    if workflow is None:
        return None, None

    run_step = await session.get(WorkflowRunStep, step_run_id)
    if run_step is None:
        return None, None
    run = await session.get(WorkflowRun, run_step.run_id)
    if run is None or run.workflow_id != workflow_id:
        return None, None

    # A run that hasn't finished still owns its steps' agents: replaying
    # underneath one would race the coordinator (or, for a paused run, collide
    # with the resume that re-drives the parked step) over the same agent.
    if workflow.status in ("running", "paused", "awaiting_approval"):
        return workflow, "Stop the run before replaying a step."
    if run_step.agent_id is None:
        return workflow, "This step ran no agent, so there's nothing to replay."
    if run_step.input_context == PRUNED_PLACEHOLDER:
        return workflow, "This step's input was cleared by retention, so it can't be replayed."

    agent = await session.get(AgentSession, run_step.agent_id)
    if agent is None:
        return workflow, "The agent that ran this step no longer exists."
    if agent.status in _REPLAY_BUSY_STATUSES:
        return workflow, "That step's agent is busy — wait for its current turn to end."

    # Re-apply the step's capability scope and the workflow's role onto a fresh
    # run, so the replay executes under the same conditions the pipeline gave it.
    # Best-effort: the definition may have been edited since, in which case the
    # agent's current settings stand.
    step = next(
        (
            s
            for s in _ordered_steps(workflow)
            if s.position == run_step.position and s.agent_id == run_step.agent_id
        ),
        None,
    )
    # Unconditional: a replay with no run would leave a trace row that
    # ``finalize_replays_for_run`` (which matches on ``agent_run_id``) can never
    # close, pinning the step "in flight" on the board forever.
    agent_run = await _open_agent_run(
        session,
        run_step.agent_id,
        workflow,
        step,
        trigger="replay",
        workflow_run_id=run_step.run_id,
    )

    prior_replays = await session.execute(
        select(func.count(WorkflowRunStep.id)).where(
            WorkflowRunStep.run_id == run_step.run_id,
            WorkflowRunStep.position == run_step.position,
            WorkflowRunStep.replay.is_(True),
        )
    )
    context = run_step.input_context
    replay_step = WorkflowRunStep(
        run_id=run_step.run_id,
        position=run_step.position,
        kind=run_step.kind,
        label=run_step.label,
        agent_id=run_step.agent_id,
        agent_run_id=agent_run.id if agent_run is not None else None,
        attempt=int(prior_replays.scalar() or 0) + 1,
        replay=True,
        status="running",
        input_context=context,
        started_at=datetime.now(UTC),
        token_baseline_in=0,
        token_baseline_out=0,
    )
    session.add(replay_step)
    await session.flush()
    if agent_run is not None:
        agent_run.workflow_run_step_id = replay_step.id
    await session.commit()
    await _publish(workflow)

    manager.enqueue(
        manager.start_task(
            run_step.agent_id,
            extra_context=context,
            run_id=agent_run.id if agent_run is not None else None,
        )
    )
    return workflow, None


async def finalize_replays_for_run(session: AsyncSession, agent_run_id: int) -> None:
    """Close the open replay trace this agent *run* was driving, if any.

    A replay is invisible to the coordinator by design, so nothing else would
    ever close its row — it would render as a step forever in flight. This runs
    off the same completion seam as the advance, but independently of it: a
    replay happens precisely when no run is active, which is exactly when the
    advance has nothing to do.

    Matched by run rather than by agent so a shared agent's *other* execution
    can't close a replay it never started.
    """
    agent_run = await session.get(AgentRun, agent_run_id)
    if agent_run is None:
        return
    agent_id = agent_run.agent_id
    run_step = (
        await session.execute(
            select(WorkflowRunStep)
            .where(
                WorkflowRunStep.agent_run_id == agent_run_id,
                WorkflowRunStep.replay.is_(True),
                WorkflowRunStep.finished_at.is_(None),
            )
            .order_by(WorkflowRunStep.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if run_step is None:
        return

    agent = await session.get(AgentSession, agent_id)
    status = agent_run.status
    if status in STEP_AWAITING_PERMISSION_STATUSES:
        # The turn is still alive and resumes the moment the gate is answered.
        return

    if status in STEP_FAILED_STATUSES:
        await _close_run_step(session, run_step, status="failed")
    elif status in STEP_CANCELLED_STATUSES:
        await _close_run_step(session, run_step, status="cancelled")
    elif status in STEP_BLOCKED_STATUSES:
        await _close_run_step(session, run_step, status="blocked")
    elif status in STEP_DONE_STATUSES:
        if run_step.kind == "gate":
            verdict_text = await _last_assistant_message(
                session, agent_id, agent_run.id if agent_run is not None else None
            ) or (agent_run.result_summary or "")
            passed = _parse_verdict(verdict_text)
            if _turn_dropped(agent_run, verdict_text):
                await _close_run_step(
                    session, run_step, status="failed", output_summary=_DROPPED_TURN_REASON
                )
            else:
                await _tidy_gate_result(session, agent, agent_run, verdict_text, passed)
                await _close_run_step(
                    session,
                    run_step,
                    status="passed" if passed else "failed",
                    output_summary=_verdict_reason(verdict_text),
                    gate_verdict="PASS" if passed else "FAIL",
                )
        else:
            output = await _step_output(session, agent_id, agent_run)
            dropped = _turn_dropped(agent_run, output)
            await _close_run_step(
                session,
                run_step,
                status="failed" if dropped else "completed",
                output_summary=_DROPPED_TURN_REASON if dropped else output,
            )
    else:
        # running / interrupted — not a decision point.
        return

    await session.commit()
    run = await session.get(WorkflowRun, run_step.run_id)
    workflow = await _load_workflow(session, run.workflow_id) if run is not None else None
    if workflow is not None:
        await _publish(workflow)


async def step_attempt_events(
    session: AsyncSession, workflow_id: int, step_run_id: int
) -> list[dict[str, Any]] | None:
    """The agent's normalised event stream for one step *attempt*.

    A trace row tells you what a step received and produced, but not *how* it
    got there — which is exactly what you need when a step blocks or stalls with
    no output at all. The events (tool calls, reasoning, errors) live per agent
    in ``agent_events``, so an agent re-driven four times has one continuous
    stream; this slices it to the attempt's own window so each row shows only
    its own work.

    Returns ``None`` when the attempt doesn't belong to the workflow (a 404 for
    the caller), and an empty list when it simply has no archived activity.
    """
    run_step = await session.get(WorkflowRunStep, step_run_id)
    if run_step is None:
        return None
    run = await session.get(WorkflowRun, run_step.run_id)
    if run is None or run.workflow_id != workflow_id:
        return None
    if run_step.agent_id is None or run_step.started_at is None:
        return []

    # A small lead-in: the trace row is written immediately *before* the agent is
    # enqueued, but clock granularity (and a re-used session's first event)
    # shouldn't drop the opening events of the attempt.
    start = run_step.started_at - timedelta(seconds=2)
    stmt = (
        select(AgentEventRecord.payload, AgentEventRecord.created_at)
        .where(
            AgentEventRecord.agent_session_id == run_step.agent_id,
            AgentEventRecord.created_at >= start,
        )
        .order_by(AgentEventRecord.id)
    )
    if run_step.finished_at is not None:
        # A finished attempt is bounded — but not at exactly ``finished_at``.
        # The events that *cause* the finalization (the permission request that
        # blocked it, the error that failed it) are archived microseconds after
        # the status change that closed the row, so cutting there drops the very
        # evidence you opened the trace for. Extend to the next attempt on this
        # agent instead, which is the true boundary; only when there is no next
        # attempt do we fall back to a grace window.
        next_start = (
            await session.execute(
                select(WorkflowRunStep.started_at)
                .where(
                    WorkflowRunStep.agent_id == run_step.agent_id,
                    WorkflowRunStep.id != run_step.id,
                    WorkflowRunStep.started_at > run_step.started_at,
                )
                .order_by(WorkflowRunStep.started_at)
                .limit(1)
            )
        ).scalar_one_or_none()
        end = next_start or (run_step.finished_at + timedelta(seconds=30))
        stmt = stmt.where(AgentEventRecord.created_at < end)

    events: list[dict[str, Any]] = []
    for payload, created_at in (await session.execute(stmt)).all():
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            continue
        # Streaming frames archived before they stopped being kept render nothing.
        if not isinstance(data, dict) or is_content_free(data.get("kind"), data.get("text")):
            continue
        # Older rows predate the payload carrying its own timestamp; fall back to
        # the row's so the UI can always order and label them.
        if not data.get("at") and created_at is not None:
            data["at"] = created_at.isoformat()
        events.append(data)
    return events
