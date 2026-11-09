"""The stalled-step sweep: fail steps whose agent has gone quiet past the
workflow's step timeout.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from precursor.backend.models.workflow import (
    Workflow,
    WorkflowRunStep,
)
from precursor.backend.services.definitions import overlay as definition_overlay

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from precursor.backend.services.agents.manager import AgentManager

from precursor.backend.services.agents.workflow_engine.common import (
    _load_workflow,
    _ordered_steps,
    _step_label,
    _workflow_lock,
)
from precursor.backend.services.agents.workflow_engine.steps import (
    _apply_failure_policy,
)

logger = logging.getLogger(__name__)


# --- Stall watchdog ---------------------------------------------------------


async def sweep_stalled_steps(session: AsyncSession, manager: AgentManager) -> int:
    """Fail-forward any step stuck past its workflow's ``step_timeout_seconds``.

    An agent that never returns would otherwise park an unattended pipeline in
    ``running`` forever. The watchdog cancels the wedged agent and puts the step
    through its normal ``on_error`` policy, so a timeout can retry, be skipped, or
    stop the run exactly like any other failure. Opt-in per workflow (null
    timeout = no watchdog). Returns how many runs it intervened in.
    """
    now = datetime.now(UTC)
    if definition_overlay.files_mode():
        # The timeout is declared by each workflow's (pinned) file, not the
        # column: load the rows so they're projected, and read it from there.
        running = (
            (
                await session.execute(
                    select(Workflow).where(
                        Workflow.status == "running", Workflow.current_run_id.is_not(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        candidates = [(w.id, w.step_timeout_seconds) for w in running]
    else:
        result = await session.execute(
            select(Workflow.id, Workflow.step_timeout_seconds).where(
                Workflow.status == "running",
                Workflow.step_timeout_seconds.is_not(None),
                Workflow.current_run_id.is_not(None),
            )
        )
        candidates = list(result.tuples().all())
    swept = 0
    for workflow_id, step_timeout in candidates:
        timeout = step_timeout or 0
        if timeout <= 0:
            continue
        # Under the same lock as an advance: a step finishing right as its
        # timeout elapses would otherwise be both advanced and failed forward,
        # driving the next step twice.
        async with _workflow_lock(workflow_id):
            session.expire_all()
            workflow = await _load_workflow(session, workflow_id)
            if workflow is None or workflow.status != "running" or workflow.current_run_id is None:
                continue
            open_step = (
                await session.execute(
                    select(WorkflowRunStep)
                    .where(
                        WorkflowRunStep.run_id == workflow.current_run_id,
                        WorkflowRunStep.finished_at.is_(None),
                        WorkflowRunStep.agent_id.is_not(None),
                        # A replay isn't part of the run's progress, so it must
                        # not be mistaken for the step the pipeline is stuck on.
                        WorkflowRunStep.replay.is_(False),
                    )
                    .order_by(WorkflowRunStep.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if open_step is None or open_step.started_at is None:
                continue
            started = open_step.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if (now - started).total_seconds() < timeout:
                continue

            steps = _ordered_steps(workflow)
            idx = next((i for i, s in enumerate(steps) if s.id == workflow.current_step_id), None)
            agent_id = open_step.agent_id
            if idx is None or agent_id is None:
                continue

            # Stop the wedged agent before re-driving or moving past it, so a
            # retry doesn't race a turn that may still be alive somewhere.
            try:
                manager.enqueue(manager.cancel(agent_id, run_id=open_step.agent_run_id))
            except Exception:  # pragma: no cover - manager is best-effort here
                logger.exception("Watchdog could not cancel agent %s", agent_id)

            minutes = max(1, round(timeout / 60))
            await _apply_failure_policy(
                session,
                manager,
                workflow,
                steps,
                idx,
                agent_id,
                f"Step '{_step_label(steps[idx])}' stalled with no result for over {minutes} min "
                "and was stopped by the watchdog.",
            )
            await session.commit()
            swept += 1
    return swept
