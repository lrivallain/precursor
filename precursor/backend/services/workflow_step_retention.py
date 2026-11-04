"""Age-limited retention for workflow step input context.

``workflow_run_steps.input_context`` stores the full prompt each step was handed,
so a workflow that chains large upstream outputs re-archives them once per step
and per run. One prod DB held ~73 MB of it against ~7 MB of step outputs, and
nothing bounded it.

When a window is configured, this sweep replaces ``input_context`` with a short placeholder on steps of runs
that finished before the cutoff. The step row, status, tokens and
``output_summary`` are kept, so run history and cost stats survive. The only
loss is replaying such a step, which needs its original input (see
``replay_step`` in ``services/agents/workflow.py``). Runs still in flight are
never touched. Default retention is 30 days; 0 keeps everything.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import WorkflowRun, WorkflowRunStep
from precursor.backend.services.app_settings import resolve_workflow_step_retention_days
from precursor.backend.services.sweep_result import SweepResult

logger = logging.getLogger(__name__)

# Shared with the replay guard, so a pruned step is recognised rather than
# re-run with the placeholder as its input. Also keeps the sweep idempotent.
PRUNED_PLACEHOLDER = "[Step input pruned to save space]"


async def prune_expired_workflow_step_inputs(
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] = SessionLocal,
    *,
    dry_run: bool = False,
) -> SweepResult:
    """Drop the input context of steps from long-finished runs; report the effect.

    A no-op when retention is disabled (0 days). With ``dry_run`` the rows are
    only measured, so the settings UI can preview a sweep before committing.
    """
    async with session_factory() as session:
        retention_days = await resolve_workflow_step_retention_days(session)
        if retention_days <= 0:
            return SweepResult()
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        expired_runs = select(WorkflowRun.id).where(
            WorkflowRun.finished_at.is_not(None), WorkflowRun.finished_at < cutoff
        )
        criteria = (
            WorkflowRunStep.run_id.in_(expired_runs),
            WorkflowRunStep.input_context.is_not(None),
            WorkflowRunStep.input_context != PRUNED_PLACEHOLDER,
        )
        measured = (
            await session.execute(
                select(
                    func.count(WorkflowRunStep.id),
                    func.coalesce(func.sum(func.length(WorkflowRunStep.input_context)), 0),
                ).where(*criteria)
            )
        ).one()
        rows = int(measured[0] or 0)
        # Each step keeps the placeholder, so only the excess is reclaimed.
        freed = max(int(measured[1] or 0) - rows * len(PRUNED_PLACEHOLDER), 0)
        if dry_run or not rows:
            return SweepResult(rows=rows, bytes=freed)

        result = await session.execute(
            update(WorkflowRunStep).where(*criteria).values(input_context=PRUNED_PLACEHOLDER)
        )
        cleared = int(cast("CursorResult[Any]", result).rowcount or 0)
        await session.commit()
        logger.info(
            "Cleared the input context of %d workflow step(s) from runs older than %d day(s)",
            cleared,
            retention_days,
        )
        return SweepResult(rows=cleared, bytes=freed)
