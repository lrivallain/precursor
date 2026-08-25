"""Workflows router — reusable, coordinated sequences of independent agents.

Thin HTTP surface over the workflow coordinator
(:mod:`precursor.backend.services.agents.workflow`): CRUD for the definition +
its ordered steps, plus lifecycle controls (run / pause / resume / cancel),
scheduling, and a webhook trigger. Chaining lives on the workflow, so the
referenced agents stay plain, reusable rows any workflow may point at.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from precursor.backend.db import get_session
from precursor.backend.models import (
    AgentRun,
    AgentSession,
    Workflow,
    WorkflowRun,
    WorkflowRunStep,
    WorkflowState,
    WorkflowStep,
)
from precursor.backend.models.workflow import (
    WORKFLOW_STEP_CONTEXT_MODES,
    WORKFLOW_STEP_ERROR_POLICIES,
    WORKFLOW_STEP_KINDS,
    WORKFLOW_STEP_REJECT_POLICIES,
)
from precursor.backend.schemas.agent import AgentEvent, AgentPendingPermission
from precursor.backend.schemas.workflow import (
    WorkflowApprovalRequest,
    WorkflowCreate,
    WorkflowPermissionDecision,
    WorkflowRead,
    WorkflowResumeRequest,
    WorkflowRetryRequest,
    WorkflowRunRead,
    WorkflowRunRequest,
    WorkflowScheduleUpdate,
    WorkflowStepInput,
    WorkflowUpdate,
)
from precursor.backend.schemas.workflow_state import (
    WorkflowStateRead,
    WorkflowStateWrite,
)
from precursor.backend.services import workflow_state as workflow_state_service
from precursor.backend.services.agents import workflow as workflow_svc
from precursor.backend.services.agents.manager import get_agent_manager, normalize_mcp_scope
from precursor.backend.services.app_settings import (
    resolve_agents_enabled,
    resolve_workflows_default_capabilities,
    resolve_workflows_default_step_timeout,
)
from precursor.backend.services.events import publish_workflow_changed
from precursor.backend.services.schedule_timing import compute_next_run

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


def _cap_default(enabled: bool) -> bool | None:
    """Seed a step's capability override from the configured default."""
    return None if enabled else False


def _now() -> datetime:
    return datetime.now(UTC)


async def _webhook_input(request: Request) -> str | None:
    """Best-effort brief from a webhook body.

    A caller may post ``{"input": "…"}`` (the explicit form), any other JSON
    object (handed over pretty-printed so the agent can read the fields), or raw
    text. A missing/empty/unparseable body just means "no brief" — a webhook must
    never fail to fire because its payload wasn't the shape we expected.
    """
    try:
        raw = await request.body()
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return raw.decode("utf-8", errors="replace").strip()[:8000] or None
    if isinstance(data, dict):
        explicit = data.get("input")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()[:8000]
    if isinstance(data, str):
        return data.strip()[:8000] or None
    return json.dumps(data, indent=2, ensure_ascii=False)[:8000] or None


async def _require_enabled(session: AsyncSession) -> None:
    if not await resolve_agents_enabled(session):
        raise HTTPException(status.HTTP_409_CONFLICT, "Agents mode is disabled")


async def _load(session: AsyncSession, workflow_id: int) -> Workflow:
    result = await session.execute(
        select(Workflow)
        .where(Workflow.id == workflow_id)
        .options(selectinload(Workflow.steps).selectinload(WorkflowStep.agent))
        # Overwrite any already identity-mapped instance's attributes/collections
        # from the DB — the session keeps objects alive (expire_on_commit=False),
        # so without this a post-mutation reload could return stale steps.
        .execution_options(populate_existing=True)
    )
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return workflow


async def _step_run_state(
    session: AsyncSession, workflows: list[Workflow]
) -> dict[tuple[int, int], AgentRun]:
    """Map ``(workflow_id, step position)`` to the run that step most recently drove.

    A step's board node reports what *that step* did, but its ``agent`` summary
    is an :class:`AgentSession` row — and since the execution split those columns
    are a write-through mirror of the agent's **current** run. For a reusable
    agent shared by two workflows that is the wrong answer on every board but the
    one that happened to finish last: both would claim the same result, status
    and progress (issue #242). The per-attempt truth is already recorded on
    ``WorkflowRunStep.agent_run_id``, so resolve through it.

    Only the newest workflow run is consulted — the board shows the current
    state of the pipeline, and older runs live in the trace timeline.
    """
    ids = [w.id for w in workflows]
    if not ids:
        return {}
    # Newest run per workflow.
    latest = (
        select(WorkflowRun.workflow_id, func.max(WorkflowRun.id).label("run_id"))
        .where(WorkflowRun.workflow_id.in_(ids))
        .group_by(WorkflowRun.workflow_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(latest.c.workflow_id, WorkflowRunStep.position, WorkflowRunStep.agent_run_id)
            .join(WorkflowRunStep, WorkflowRunStep.run_id == latest.c.run_id)
            .where(WorkflowRunStep.agent_run_id.is_not(None))
            # Newest attempt last, so the dict below keeps it.
            .order_by(WorkflowRunStep.id)
        )
    ).all()
    if not rows:
        return {}
    wanted = {(wf_id, position): run_id for wf_id, position, run_id in rows}
    runs = (
        (await session.execute(select(AgentRun).where(AgentRun.id.in_(set(wanted.values())))))
        .scalars()
        .all()
    )
    by_id = {run.id: run for run in runs}
    return {key: by_id[run_id] for key, run_id in wanted.items() if run_id in by_id}


async def _read(session: AsyncSession, workflows: list[Workflow]) -> list[WorkflowRead]:
    """Serialise workflows, folding each step agent's *live* state into it.

    Narration and a parked permission request exist only in the runtime's
    in-memory view, so returning ORM rows straight to FastAPI silently leaves
    them null. The permission in particular has to reach the board: a gate on an
    **inline** step is otherwise unanswerable anywhere, because its vessel is
    hidden from the Agents roster.

    Execution-state fields are then re-pointed at the run each step actually
    drove, so a shared agent doesn't broadcast one workflow's outcome onto every
    board that references it.
    """
    reads = [WorkflowRead.model_validate(w) for w in workflows]
    agent_ids = [s.agent_id for w in workflows for s in w.steps if s.agent_id is not None]
    if not agent_ids:
        return reads
    activity = get_agent_manager().live_activity(agent_ids)
    per_step = await _step_run_state(session, workflows)
    for read in reads:
        for step in read.steps:
            if step.agent is None:
                continue
            live = activity.get(step.agent.id) or {}
            step.agent.active_narration = live.get("active_narration")
            pending = live.get("pending_permission")
            step.agent.pending_permission = (
                AgentPendingPermission.model_validate(pending) if pending else None
            )
            run = per_step.get((read.id, step.position))
            if run is None:
                # Never run here: the agent's own mirror is all there is, and for
                # a private vessel it is also exactly right.
                continue
            step.agent.status = run.status
            step.agent.result_summary = run.result_summary
            step.agent.blocked_question = run.blocked_question
            step.agent.progress = run.progress
            step.agent.progress_label = run.progress_label
            step.agent.finished_at = run.finished_at
    return reads


async def _read_one(session: AsyncSession, workflow: Workflow) -> WorkflowRead:
    return (await _read(session, [workflow]))[0]


async def _apply_steps(
    session: AsyncSession, workflow: Workflow, steps: list[WorkflowStepInput]
) -> None:
    """Replace the workflow's ordered steps in one shot.

    Each input either references an existing agent by ``agent_id`` or authors one
    from ``task`` — the workflow owns the chaining, so an authored agent is
    unattached and just waits for the coordinator to run it. ``reusable`` decides
    whether that agent joins the Agents section or stays the step's private
    vessel.
    """
    # Drop existing steps via the ORM (removes them from the identity map and any
    # loaded relationship collection, so they aren't re-inserted on the next
    # flush). Querying explicitly avoids awaiting the lazy relationship on a
    # freshly-created workflow row.
    existing = (
        (await session.execute(select(WorkflowStep).where(WorkflowStep.workflow_id == workflow.id)))
        .scalars()
        .all()
    )
    # Agents owned by this workflow's inline steps. The step rows are recreated
    # wholesale on every save, so we track these to (a) reuse the same vessel
    # when an inline step is re-saved — keeping its history — and (b) delete the
    # ones no step points at any more, rather than orphaning them invisibly.
    prior_inline: set[int] = set()
    for step in existing:
        if step.agent_id is not None:
            owned = await session.get(AgentSession, step.agent_id)
            if owned is not None and owned.inline:
                prior_inline.add(step.agent_id)
        await session.delete(step)
    await session.flush()

    # Settings → Workflows seeds what a *new* step may draw on. An explicit value
    # in the payload always wins; this only fills the "not stated" case, so an
    # operator who defaults tools off doesn't have to switch every step by hand.
    caps = await resolve_workflows_default_capabilities(session)

    kept_inline: set[int] = set()
    for pos, item in enumerate(steps):
        kind = item.kind if item.kind in WORKFLOW_STEP_KINDS else "task"
        agent_id = item.agent_id
        if kind == "approval":
            # A human checkpoint runs no agent — ignore any agent/task supplied.
            agent_id = None
        elif (item.task or "").strip():
            # The step authors its own prompt. Where the resulting agent *lives*
            # is then the author's call: by default it belongs to the step — a
            # private vessel, hidden from the Agents list and deleted with the
            # step — while ``reusable`` mints a real agent in the Agents section
            # instead, so a pipeline can create one without leaving the builder.
            # This is keyed off "was the prompt written here?" rather than the
            # step's kind, so a gate can be a one-off check just as a task can be
            # a one-off job.
            task = (item.task or "").strip()
            # A hidden vessel is named after its step: the step label if one was
            # given, else the opening of its own prompt. Nothing to type — the
            # name only ever surfaces as the step's fallback label. A reusable
            # agent is the other way round: its own name wins, because that name
            # is what it's recognised by everywhere else.
            if item.reusable:
                title = (item.title or item.name or task).strip()[:200] or "New agent"
            else:
                title = (item.name or item.title or task).strip()[:200] or (
                    "Inline gate" if kind == "gate" else "Inline step"
                )
            agent = await session.get(AgentSession, agent_id) if agent_id else None
            if agent is not None and agent.inline:
                # Re-save: update the vessel in place so the step keeps its run
                # history instead of silently becoming a different agent.
                agent.title = title
                agent.task_prompt = task
                agent.model = item.model
                kept_inline.add(agent.id)
            else:
                agent = AgentSession(
                    title=title,
                    task_prompt=task,
                    model=item.model,
                    status="waiting",
                    inline=not item.reusable,
                )
                session.add(agent)
                await session.flush()
            agent_id = agent.id
        elif agent_id is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Each step needs an existing agent, or instructions to run inline",
            )
        else:
            referenced = await session.get(AgentSession, agent_id)
            if referenced is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"Agent {agent_id} not found")
            if referenced.inline:
                # A partial edit: the step still points at its private vessel but
                # didn't resend the prompt. Claim it, or the sweep below would
                # delete the very agent this request just attached — silently,
                # since the response still carries the ``agent_id``.
                kept_inline.add(referenced.id)
        session.add(
            WorkflowStep(
                workflow_id=workflow.id,
                agent_id=agent_id,
                position=pos,
                name=item.name,
                kind=kind,
                on_fail_position=item.on_fail_position,
                instructions=(item.instructions or "").strip() or None,
                on_error=(
                    item.on_error if item.on_error in WORKFLOW_STEP_ERROR_POLICIES else "fail"
                ),
                max_retries=item.max_retries,
                on_reject=(
                    item.on_reject if item.on_reject in WORKFLOW_STEP_REJECT_POLICIES else "rework"
                ),
                context_mode=(
                    item.context_mode
                    if item.context_mode in WORKFLOW_STEP_CONTEXT_MODES
                    else "auto"
                ),
                context_sources=(item.context_sources or "").strip() or None,
                # A default of *on* leaves the override null so the step simply
                # inherits its agent; a default of *off* is written explicitly,
                # because "inherit" would quietly turn it back on.
                use_mcp=item.use_mcp if item.use_mcp is not None else _cap_default(caps["use_mcp"]),
                use_skills=(
                    item.use_skills
                    if item.use_skills is not None
                    else _cap_default(caps["use_skills"])
                ),
                use_memory=(
                    item.use_memory
                    if item.use_memory is not None
                    else _cap_default(caps["use_memory"])
                ),
                mcp_servers=normalize_mcp_scope(item.mcp_servers),
            )
        )
    await session.flush()

    # An inline vessel whose step was deleted (or converted to another kind) has
    # no owner left — remove it so it can't linger as an invisible agent.
    for orphan_id in prior_inline - kept_inline:
        orphan = await session.get(AgentSession, orphan_id)
        if orphan is None or not orphan.inline:
            continue
        # SQLite runs with foreign keys off, so the step→agent ``ON DELETE SET
        # NULL`` never fires there: detach anything still pointing at the vessel
        # ourselves rather than leaving a dangling ``agent_id`` behind. Reachable
        # across workflows — another pipeline may reference this vessel by id.
        await session.execute(
            update(WorkflowStep).where(WorkflowStep.agent_id == orphan_id).values(agent_id=None)
        )
        await session.delete(orphan)
    await session.flush()


@router.get("", response_model=list[WorkflowRead])
async def list_workflows(
    include_archived: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[WorkflowRead]:
    stmt = (
        select(Workflow)
        .options(selectinload(Workflow.steps).selectinload(WorkflowStep.agent))
        .order_by(Workflow.updated_at.desc())
    )
    if not include_archived:
        stmt = stmt.where(Workflow.archived_at.is_(None))
    result = await session.execute(stmt)
    return await _read(session, list(result.scalars().unique().all()))


@router.get("/archived", response_model=list[WorkflowRead])
async def list_archived_workflows(
    session: AsyncSession = Depends(get_session),
) -> list[WorkflowRead]:
    """Archived workflows, newest first — the shared Archive panel's source.

    Mirrors ``/api/topics/archived`` and friends so archiving works the same way
    across every surface.
    """
    result = await session.execute(
        select(Workflow)
        .options(selectinload(Workflow.steps).selectinload(WorkflowStep.agent))
        .where(Workflow.archived_at.is_not(None))
        .order_by(Workflow.archived_at.desc())
    )
    return await _read(session, list(result.scalars().unique().all()))


@router.post("", response_model=WorkflowRead, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: WorkflowCreate,
    session: AsyncSession = Depends(get_session),
) -> WorkflowRead:
    await _require_enabled(session)
    workflow = Workflow(
        name=payload.name.strip()[:200] or "Workflow",
        description=payload.description,
        icon=payload.icon,
        color=payload.color,
        clear_artifacts=payload.clear_artifacts,
        max_loops=payload.max_loops,
        step_timeout_seconds=(
            payload.step_timeout_seconds
            if payload.step_timeout_seconds is not None
            else (await resolve_workflows_default_step_timeout(session)) or None
        ),
        role_id=payload.role_id,
        approval_policy=payload.approval_policy,
        status="draft",
    )
    session.add(workflow)
    await session.flush()
    await _apply_steps(session, workflow, payload.steps)
    # A workflow with at least one step is ready to run (idle); an empty one
    # stays a draft until steps are added.
    workflow.status = "idle" if payload.steps else "draft"
    await session.commit()
    await publish_workflow_changed(workflow.id)
    return await _read_one(session, await _load(session, workflow.id))


@router.get("/{workflow_id}", response_model=WorkflowRead)
async def get_workflow(
    workflow_id: int, session: AsyncSession = Depends(get_session)
) -> WorkflowRead:
    return await _read_one(session, await _load(session, workflow_id))


@router.get("/{workflow_id}/runs", response_model=list[WorkflowRunRead])
async def list_workflow_runs(
    workflow_id: int,
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
) -> list[WorkflowRun]:
    """Recent run traces (newest first), each with its ordered per-step attempts.

    The durable history behind the workflow page's trace timeline and run picker:
    every execution records its steps' inputs, outputs, and gate verdicts.
    """
    limit = max(1, min(limit, 100))
    result = await session.execute(
        select(WorkflowRun)
        .where(WorkflowRun.workflow_id == workflow_id)
        .order_by(WorkflowRun.run_number.desc())
        .limit(limit)
        .options(selectinload(WorkflowRun.step_runs))
    )
    return list(result.scalars().unique().all())


@router.get("/{workflow_id}/run-steps/{step_run_id}/events", response_model=list[AgentEvent])
async def get_step_attempt_events(
    workflow_id: int,
    step_run_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """The agent's activity (tool calls, reasoning, errors) for one attempt.

    The trace shows what a step received and produced; this is *how* it got
    there — the detail you need when a step blocks or stalls having produced no
    output at all. Sliced to the attempt's own window, so an agent re-driven
    several times doesn't replay its whole history under every row.
    """
    events = await workflow_svc.step_attempt_events(session, workflow_id, step_run_id)
    if events is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Step attempt not found")
    return events


@router.patch("/{workflow_id}", response_model=WorkflowRead)
async def update_workflow(
    workflow_id: int,
    payload: WorkflowUpdate,
    session: AsyncSession = Depends(get_session),
) -> WorkflowRead:
    workflow = await _load(session, workflow_id)
    if payload.name is not None:
        workflow.name = payload.name.strip()[:200] or workflow.name
    if payload.description is not None:
        workflow.description = payload.description
    # An icon is optional, so ``null`` is a *meaningful* value (clear it) rather
    # than "field omitted". Key off what the client actually sent.
    if "icon" in payload.model_fields_set:
        workflow.icon = (payload.icon or "").strip() or None
    if payload.color is not None:
        workflow.color = payload.color
    if payload.clear_artifacts is not None:
        workflow.clear_artifacts = payload.clear_artifacts
    if payload.max_loops is not None:
        workflow.max_loops = payload.max_loops
    if payload.step_timeout_seconds is not None:
        # 0 is the "turn the watchdog off" signal (null isn't distinguishable
        # from "field omitted" in a partial update).
        workflow.step_timeout_seconds = payload.step_timeout_seconds or None
    if payload.role_id is not None:
        # Same convention: 0 clears the role.
        workflow.role_id = payload.role_id or None
    if "approval_policy" in payload.model_fields_set:
        # ``None`` is meaningful (inherit each agent's own policy), so this keys
        # off what the client actually sent rather than the value alone.
        workflow.approval_policy = payload.approval_policy
    if payload.steps is not None:
        if workflow.status == "running":
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Cannot edit steps while the workflow is running"
            )
        await _apply_steps(session, workflow, payload.steps)
        # Editing steps returns the workflow to draft/idle if it was terminal.
        if workflow.status in ("completed", "failed", "cancelled", "draft"):
            workflow.status = "idle" if payload.steps else "draft"
        workflow.current_step_id = None
    await session.commit()
    await publish_workflow_changed(workflow.id)
    return await _read_one(session, await _load(session, workflow.id))


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(workflow_id: int, session: AsyncSession = Depends(get_session)) -> None:
    workflow = await _load(session, workflow_id)
    # Inline steps own private agents. Deleting the workflow cascades to its
    # steps, but the vessels themselves would survive as invisible orphans
    # (the step→agent FK is SET NULL), so collect and remove them here.
    vessels = [s.agent_id for s in workflow.steps if s.agent_id is not None]
    await session.delete(workflow)
    await session.flush()
    for agent_id in vessels:
        agent = await session.get(AgentSession, agent_id)
        if agent is not None and agent.inline:
            await session.delete(agent)
    await session.commit()
    await publish_workflow_changed(workflow_id)


# --- Lifecycle controls ----------------------------------------------------


@router.post("/{workflow_id}/run", response_model=WorkflowRead)
async def run_workflow(
    workflow_id: int,
    body: WorkflowRunRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
) -> WorkflowRead:
    """Start a run, optionally with a per-run brief.

    The body is optional so the plain "just run it" call keeps working; when
    supplied, ``input`` becomes the run's subject and is fed to every step.
    """
    await _require_enabled(session)
    workflow = await workflow_svc.start_workflow(
        session,
        get_agent_manager(),
        workflow_id,
        run_input=body.input if body else None,
    )
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return await _read_one(session, await _load(session, workflow_id))


@router.post("/{workflow_id}/pause", response_model=WorkflowRead)
async def pause_workflow(
    workflow_id: int, session: AsyncSession = Depends(get_session)
) -> WorkflowRead:
    workflow = await workflow_svc.pause_workflow(session, workflow_id)
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return await _read_one(session, await _load(session, workflow_id))


@router.post("/{workflow_id}/resume", response_model=WorkflowRead)
async def resume_workflow(
    workflow_id: int,
    body: WorkflowResumeRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
) -> WorkflowRead:
    """Resume a paused run, optionally answering what parked it.

    The body is optional so a plain "carry on" resume keeps working; when the
    pause came from a step's agent **blocking** on a question, ``input`` is the
    answer, injected into the resumed step so it isn't re-driven blind.
    """
    await _require_enabled(session)
    workflow = await workflow_svc.resume_workflow(
        session,
        get_agent_manager(),
        workflow_id,
        guidance=body.input if body else None,
    )
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return await _read_one(session, await _load(session, workflow_id))


@router.post("/{workflow_id}/permission", response_model=WorkflowRead)
async def resolve_step_permission(
    workflow_id: int,
    body: WorkflowPermissionDecision,
    session: AsyncSession = Depends(get_session),
) -> WorkflowRead:
    """Answer the tool-permission gate parking a step, and resume the run.

    The board is the only place this decision can be made for an **inline**
    step, whose agent is hidden from the Agents section. Resolving it also puts
    the paused run back to ``running`` — otherwise the approved agent finishes
    its turn into a pipeline that stopped listening when it blocked.
    """
    await _require_enabled(session)
    workflow, resolved = await workflow_svc.resolve_step_permission(
        session,
        get_agent_manager(),
        workflow_id,
        request_id=body.request_id,
        decision=body.decision,
    )
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    if not resolved:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That permission request is no longer waiting — it was already answered or cancelled.",
        )
    return await _read_one(session, await _load(session, workflow_id))


@router.post("/{workflow_id}/retry", response_model=WorkflowRead)
async def retry_workflow_step(
    workflow_id: int,
    body: WorkflowRetryRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
) -> WorkflowRead:
    """Re-drive one step of a stopped run as a fresh attempt, in place.

    Picks a failed run back up at the step that broke instead of re-running the
    pipeline from the top: the retry appends a new attempt to the *same* run
    trace and carries on from there, so the good steps before it are neither
    thrown away nor paid for twice.
    """
    await _require_enabled(session)
    workflow = await workflow_svc.retry_step(
        session,
        get_agent_manager(),
        workflow_id,
        position=body.position if body else None,
        guidance=body.input if body else None,
    )
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return await _read_one(session, await _load(session, workflow_id))


@router.post("/{workflow_id}/run-steps/{step_run_id}/replay", response_model=WorkflowRead)
async def replay_run_step(
    workflow_id: int,
    step_run_id: int,
    session: AsyncSession = Depends(get_session),
) -> WorkflowRead:
    """Run one recorded step attempt again, on its own, with the input it saw.

    Unlike ``/retry`` — which recovers a *stopped run* and carries on through the
    rest of the pipeline — this replays a single step in isolation and advances
    nothing, so it works on a run that succeeded. The new attempt lands in the
    same run trace, marked as a replay.
    """
    await _require_enabled(session)
    workflow, refusal = await workflow_svc.replay_step(
        session,
        get_agent_manager(),
        workflow_id,
        step_run_id=step_run_id,
    )
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Step attempt not found")
    if refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, refusal)
    return await _read_one(session, await _load(session, workflow_id))


@router.post("/{workflow_id}/cancel", response_model=WorkflowRead)
async def cancel_workflow(
    workflow_id: int, session: AsyncSession = Depends(get_session)
) -> WorkflowRead:
    workflow = await workflow_svc.cancel_workflow(session, get_agent_manager(), workflow_id)
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return await _read_one(session, await _load(session, workflow_id))


@router.post("/{workflow_id}/approve", response_model=WorkflowRead)
async def approve_workflow_step(
    workflow_id: int,
    body: WorkflowApprovalRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
) -> WorkflowRead:
    """Clear a human approval checkpoint so the pipeline carries on."""
    await _require_enabled(session)
    workflow = await workflow_svc.approve_step(
        session, get_agent_manager(), workflow_id, note=body.note if body else None
    )
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return await _read_one(session, await _load(session, workflow_id))


@router.post("/{workflow_id}/reject", response_model=WorkflowRead)
async def reject_workflow_step(
    workflow_id: int,
    body: WorkflowApprovalRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
) -> WorkflowRead:
    """Send the work back from an approval checkpoint, with feedback.

    What happens next follows the step's ``on_reject`` policy (rework / stop /
    skip); pass ``action`` to override it for this decision.
    """
    await _require_enabled(session)
    workflow = await workflow_svc.reject_step(
        session,
        get_agent_manager(),
        workflow_id,
        feedback=body.note if body else None,
        action=body.action if body else None,
    )
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return await _read_one(session, await _load(session, workflow_id))


@router.post("/{workflow_id}/archive", response_model=WorkflowRead)
async def archive_workflow(
    workflow_id: int, session: AsyncSession = Depends(get_session)
) -> WorkflowRead:
    workflow = await _load(session, workflow_id)
    workflow.archived_at = _now()
    await session.commit()
    await publish_workflow_changed(workflow_id)
    return await _read_one(session, await _load(session, workflow_id))


@router.post("/{workflow_id}/unarchive", response_model=WorkflowRead)
async def unarchive_workflow(
    workflow_id: int, session: AsyncSession = Depends(get_session)
) -> WorkflowRead:
    workflow = await _load(session, workflow_id)
    workflow.archived_at = None
    await session.commit()
    await publish_workflow_changed(workflow_id)
    return await _read_one(session, await _load(session, workflow_id))


# --- Scheduling ------------------------------------------------------------


@router.put("/{workflow_id}/schedule", response_model=WorkflowRead)
async def update_schedule(
    workflow_id: int,
    payload: WorkflowScheduleUpdate,
    session: AsyncSession = Depends(get_session),
) -> WorkflowRead:
    workflow = await _load(session, workflow_id)
    if payload.interval_seconds is not None:
        workflow.interval_seconds = payload.interval_seconds
    if payload.run_at_minute is not None:
        workflow.run_at_minute = payload.run_at_minute
    if payload.timezone is not None:
        workflow.timezone = payload.timezone
    if payload.days_of_week is not None:
        workflow.days_of_week = payload.days_of_week
    if payload.schedule_enabled is not None:
        workflow.schedule_enabled = payload.schedule_enabled

    if workflow.schedule_enabled and (
        workflow.interval_seconds is not None or workflow.run_at_minute is not None
    ):
        workflow.next_run_at = compute_next_run(
            _now(),
            workflow.interval_seconds or 86400,
            workflow.days_of_week,
            workflow.run_at_minute,
            workflow.timezone,
        )
    elif not workflow.schedule_enabled:
        workflow.next_run_at = None
    await session.commit()
    await publish_workflow_changed(workflow_id)
    return await _read_one(session, await _load(session, workflow_id))


# --- Webhook trigger -------------------------------------------------------


@router.post("/{workflow_id}/webhook", response_model=WorkflowRead)
async def mint_webhook(
    workflow_id: int, session: AsyncSession = Depends(get_session)
) -> WorkflowRead:
    workflow = await _load(session, workflow_id)
    workflow.webhook_token = Workflow.mint_webhook_token()
    await session.commit()
    await publish_workflow_changed(workflow_id)
    return await _read_one(session, await _load(session, workflow_id))


@router.delete("/{workflow_id}/webhook", response_model=WorkflowRead)
async def revoke_webhook(
    workflow_id: int, session: AsyncSession = Depends(get_session)
) -> WorkflowRead:
    workflow = await _load(session, workflow_id)
    workflow.webhook_token = None
    await session.commit()
    await publish_workflow_changed(workflow_id)
    return await _read_one(session, await _load(session, workflow_id))


@router.post("/hooks/{token}", status_code=status.HTTP_202_ACCEPTED)
async def fire_webhook(
    token: str, request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    """Start a run from an external webhook. Public (token-authenticated).

    Any request body is forwarded as the run's **brief**, so a hook can point the
    same pipeline at a different subject each time (``{"input": "…"}`` for a plain
    brief, or an arbitrary JSON/text payload, which is handed over verbatim).
    """
    result = await session.execute(select(Workflow).where(Workflow.webhook_token == token))
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown webhook token")
    if not await resolve_agents_enabled(session):
        raise HTTPException(status.HTTP_409_CONFLICT, "Agents mode is disabled")
    await workflow_svc.start_workflow(
        session,
        get_agent_manager(),
        workflow.id,
        trigger="webhook",
        run_input=await _webhook_input(request),
    )
    return {"status": "started", "workflow": str(workflow.id)}


# ------------------------------------------------------ state (pipeline memory)
#
# The workflow's own named values: written by its steps (or here by hand), read
# by later steps through ``{{state.<key>}}`` placeholders, and kept **across
# runs** — unlike the run trace and the artifact blackboard, which describe a
# single execution.


@router.get("/{workflow_id}/state", response_model=list[WorkflowStateRead])
async def list_workflow_state(
    workflow_id: int, session: AsyncSession = Depends(get_session)
) -> list[WorkflowState]:
    workflow = await _load(session, workflow_id)
    return await workflow_state_service.list_states(session, workflow.id)


@router.put("/{workflow_id}/state", response_model=WorkflowStateRead)
async def set_workflow_state(
    workflow_id: int,
    payload: WorkflowStateWrite,
    session: AsyncSession = Depends(get_session),
) -> WorkflowState:
    """Upsert one entry (same semantics as the ``workflow_state_set`` MCP tool).

    Setting a value by hand is how you seed a pipeline's first run — e.g. the
    cursor it should start from — without having to fake a run to write it.
    """
    workflow = await _load(session, workflow_id)
    try:
        state, _created = await workflow_state_service.set_state(session, workflow.id, payload)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return state


@router.delete("/{workflow_id}/state/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow_state(
    workflow_id: int, key: str, session: AsyncSession = Depends(get_session)
) -> None:
    workflow = await _load(session, workflow_id)
    if not await workflow_state_service.delete_state(session, workflow.id, key.strip().lower()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "State key not found")


@router.delete("/{workflow_id}/state", status_code=status.HTTP_204_NO_CONTENT)
async def clear_workflow_state(
    workflow_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    """Wipe the pipeline's memory — the "start clean" lever when a saved cursor
    has gone bad and every run is now working from a wrong baseline."""
    workflow = await _load(session, workflow_id)
    await workflow_state_service.clear_states(session, workflow.id)
