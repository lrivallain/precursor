"""Shared workflow-coordinator state: step statuses, the per-workflow advance
lock, loading and publishing a workflow, and walking its ordered steps.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from precursor.backend.models.agent_event import AgentEventRecord
from precursor.backend.models.agent_run import AgentRun
from precursor.backend.models.workflow import (
    WORKFLOW_PRODUCING_KINDS,
    Workflow,
    WorkflowStep,
)
from precursor.backend.services.agents.mcp_scope import parse_mcp_scope, scope_includes_precursor
from precursor.backend.services.events import publish_workflow_changed

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# A step's agent reaching one of these means "this step is done, advance."
STEP_DONE_STATUSES = ("idle", "completed")
# These divert the whole run instead of advancing.
STEP_FAILED_STATUSES = ("failed",)
# The agent stopped and *raised a question*: its turn is over and a human has to
# answer before it can be re-driven, so the run parks.
STEP_BLOCKED_STATUSES = ("blocked",)
# The agent is waiting on a tool-permission decision. Deliberately NOT a blocked
# status: the turn is still alive and resumes by itself the moment the gate is
# answered. Pausing the run for it meant every single tool call in a step closed
# the trace and demanded a manual "Resume" — an agent making five calls blocked
# five times — so the run stays ``running`` and only the card is surfaced.
STEP_AWAITING_PERMISSION_STATUSES = ("needs_approval",)
STEP_CANCELLED_STATUSES = ("cancelled",)

# --- Advance serialisation --------------------------------------------------
# Advances arrive as fire-and-forget tasks from the manager's completion seam, so
# two can run concurrently for the same workflow in separate sessions. Without a
# lock both read ``current_step_id`` still pointing at the step that just
# finished, so both advance it: two trace rows, two real ``start_task`` launches
# for one logical step entry, and the step's tokens counted twice in the run
# rollup. One lock per workflow, in-process (Precursor is a single uvicorn
# process, so this is the whole coordinator).
_advance_locks: dict[int, asyncio.Lock] = {}


@contextlib.asynccontextmanager
async def _workflow_lock(workflow_id: int) -> AsyncIterator[None]:
    """Hold the advance lock for one workflow.

    Callers **must** re-read the workflow inside the lock: whoever held it before
    them has very likely just moved ``current_step_id`` on.
    """
    lock = _advance_locks.setdefault(workflow_id, asyncio.Lock())
    async with lock:
        yield


async def _publish(workflow: Workflow) -> None:
    """Broadcast a workflow change with the state the client needs to notify on."""
    await publish_workflow_changed(workflow.id, status=workflow.status, name=workflow.name)


async def _load_workflow(
    session: AsyncSession, workflow_id: int, *, fresh: bool = False
) -> Workflow | None:
    stmt = (
        select(Workflow)
        .where(Workflow.id == workflow_id)
        .options(selectinload(Workflow.steps).selectinload(WorkflowStep.agent))
    )
    if fresh:
        # Replace identity-mapped state, e.g. after the step rows were rewritten.
        stmt = stmt.execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def _last_assistant_message(
    session: AsyncSession, agent_id: int, agent_run_id: int | None = None
) -> str | None:
    """The newest persisted ``assistant_message`` body (uncapped).

    Tier-1 hand-off: an agent that ends its turn with ``OBJECTIVE_COMPLETE:`` has
    its ``result_summary`` folded to the terse directive summary, losing the full
    output. The durable event archive still holds the complete assistant message,
    so a bare generative step ("tell me a story") forwards its whole body to the
    next step even after the directive fold.

    Scope this to ``agent_run_id`` whenever the caller knows which execution it
    is asking about. The archive spans every run of the agent — that is what
    makes it the transcript a user reads — so an agent-wide read of "the newest
    message" is really "whichever concurrent run spoke last". For a gate that
    means workflow B can pick up workflow A's ``PASS`` and wave a failing
    deliverable through. The agent-wide read stays the default only for callers
    with no run in hand.
    """
    query = select(AgentEventRecord.payload).where(AgentEventRecord.agent_session_id == agent_id)
    if agent_run_id is not None:
        query = query.where(AgentEventRecord.agent_run_id == agent_run_id)
    rows = await session.execute(query.order_by(AgentEventRecord.id.desc()).limit(100))
    for payload in rows.scalars():
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if data.get("kind") == "assistant_message":
            text = (data.get("text") or "").strip()
            if text:
                return text
    return None


def _ordered_steps(workflow: Workflow) -> list[WorkflowStep]:
    return sorted(workflow.steps, key=lambda s: s.position)


def _is_runnable(step: WorkflowStep) -> bool:
    """Can the coordinator drive into this step?

    Normally that means it has an agent — but an ``approval`` step is driven by a
    *human*, so it's runnable with no agent attached. A step whose agent was
    deleted stays un-runnable and is skipped rather than stalling the run.
    """
    return step.kind == "approval" or step.agent_id is not None


def _first_runnable(steps: list[WorkflowStep]) -> WorkflowStep | None:
    for step in steps:
        if _is_runnable(step):
            return step
    return None


def _prev_runnable_idx(steps: list[WorkflowStep], idx: int) -> int | None:
    """Index of the nearest runnable step before ``idx`` (skips broken steps)."""
    for j in range(idx - 1, -1, -1):
        if _is_runnable(steps[j]):
            return j
    return None


def _next_runnable_idx(steps: list[WorkflowStep], idx: int) -> int | None:
    for j in range(idx + 1, len(steps)):
        if _is_runnable(steps[j]):
            return j
    return None


def _prev_content_idx(steps: list[WorkflowStep], idx: int) -> int | None:
    """Index of the nearest **content-producing** step before ``idx``.

    Gates are *transparent* to the data flow: they judge the material handed to
    them and emit only a terse verdict (``PASS: …``), so forwarding a gate's own
    output downstream would drop the real payload (e.g. the story a later step
    must record). Approval steps are transparent for the same reason — a human
    saying "yes" produces no content. This skips both (and missing-agent steps)
    to find the last step that actually produced content — so a
    `task → gate → task` chain feeds the second task the first task's output,
    not the gate's verdict.
    """
    for j in range(idx - 1, -1, -1):
        if steps[j].agent_id is not None and steps[j].kind in WORKFLOW_PRODUCING_KINDS:
            return j
    return None


def _earlier_content_agent_ids(steps: list[WorkflowStep], before_idx: int | None) -> list[int]:
    """Agent ids of content-producing steps strictly before ``before_idx`` (oldest first).

    Feeds the workflow blackboard: everything published *before* the immediate
    previous producer, so a later step inherits the whole accumulated trail of
    artifacts — not just the last hand-off. Gates and approval steps (neither of
    which publishes deliverables) and missing-agent steps are skipped.
    ``before_idx`` is the index of the immediate producer; ``None`` means there is
    no prior producer, so no trail.
    """
    if before_idx is None:
        return []
    out: list[int] = []
    for j in range(0, before_idx):
        step = steps[j]
        if step.agent_id is not None and step.kind in WORKFLOW_PRODUCING_KINDS:
            out.append(step.agent_id)
    return out


def _step_label(step: WorkflowStep) -> str:
    if step.name:
        return step.name
    if step.agent is not None and step.agent.title:
        return step.agent.title
    if step.kind == "approval":
        return "Human approval"
    return f"Step {step.position + 1}"


def _has_precursor_tools(step: WorkflowStep) -> bool:
    """Whether ``step`` can call the built-in ``precursor`` MCP tools.

    Defers to the same predicate the attach path uses, so the context we
    assemble cannot promise a tool the step will not be given.
    """
    if step.use_mcp is False:
        return False
    return scope_includes_precursor(parse_mcp_scope(step.mcp_servers))


async def _current_run_for(
    session: AsyncSession,
    agent_id: int,
    workflow_run_id: int | None,
) -> AgentRun | None:
    """The newest run this agent opened for a given workflow run."""
    if workflow_run_id is None:
        return None
    result = await session.execute(
        select(AgentRun)
        .where(AgentRun.agent_id == agent_id, AgentRun.workflow_run_id == workflow_run_id)
        .order_by(AgentRun.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
