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

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from precursor.backend.db import get_session
from precursor.backend.models import (
    AgentSession,
    Workflow,
    WorkflowRun,
    WorkflowStep,
)
from precursor.backend.models.workflow import (
    WORKFLOW_STEP_CONTEXT_MODES,
    WORKFLOW_STEP_ERROR_POLICIES,
    WORKFLOW_STEP_KINDS,
    WORKFLOW_STEP_REJECT_POLICIES,
)
from precursor.backend.schemas.workflow import (
    WorkflowApprovalRequest,
    WorkflowCreate,
    WorkflowRead,
    WorkflowRunRead,
    WorkflowRunRequest,
    WorkflowScheduleUpdate,
    WorkflowStepInput,
    WorkflowUpdate,
)
from precursor.backend.services.agents import workflow as workflow_svc
from precursor.backend.services.agents.manager import get_agent_manager
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
            if await session.get(AgentSession, agent_id) is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"Agent {agent_id} not found")
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
            )
        )
    await session.flush()

    # An inline vessel whose step was deleted (or converted to another kind) has
    # no owner left — remove it so it can't linger as an invisible agent.
    for orphan_id in prior_inline - kept_inline:
        orphan = await session.get(AgentSession, orphan_id)
        if orphan is not None and orphan.inline:
            await session.delete(orphan)
    await session.flush()


@router.get("", response_model=list[WorkflowRead])
async def list_workflows(
    include_archived: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[Workflow]:
    stmt = (
        select(Workflow)
        .options(selectinload(Workflow.steps).selectinload(WorkflowStep.agent))
        .order_by(Workflow.updated_at.desc())
    )
    if not include_archived:
        stmt = stmt.where(Workflow.archived_at.is_(None))
    result = await session.execute(stmt)
    return list(result.scalars().unique().all())


@router.get("/archived", response_model=list[WorkflowRead])
async def list_archived_workflows(
    session: AsyncSession = Depends(get_session),
) -> list[Workflow]:
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
    return list(result.scalars().unique().all())


@router.post("", response_model=WorkflowRead, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: WorkflowCreate,
    session: AsyncSession = Depends(get_session),
) -> Workflow:
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
    return await _load(session, workflow.id)


@router.get("/{workflow_id}", response_model=WorkflowRead)
async def get_workflow(workflow_id: int, session: AsyncSession = Depends(get_session)) -> Workflow:
    return await _load(session, workflow_id)


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


@router.patch("/{workflow_id}", response_model=WorkflowRead)
async def update_workflow(
    workflow_id: int,
    payload: WorkflowUpdate,
    session: AsyncSession = Depends(get_session),
) -> Workflow:
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
    return await _load(session, workflow.id)


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
) -> Workflow:
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
    return await _load(session, workflow_id)


@router.post("/{workflow_id}/pause", response_model=WorkflowRead)
async def pause_workflow(
    workflow_id: int, session: AsyncSession = Depends(get_session)
) -> Workflow:
    workflow = await workflow_svc.pause_workflow(session, workflow_id)
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return await _load(session, workflow_id)


@router.post("/{workflow_id}/resume", response_model=WorkflowRead)
async def resume_workflow(
    workflow_id: int, session: AsyncSession = Depends(get_session)
) -> Workflow:
    await _require_enabled(session)
    workflow = await workflow_svc.resume_workflow(session, get_agent_manager(), workflow_id)
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return await _load(session, workflow_id)


@router.post("/{workflow_id}/cancel", response_model=WorkflowRead)
async def cancel_workflow(
    workflow_id: int, session: AsyncSession = Depends(get_session)
) -> Workflow:
    workflow = await workflow_svc.cancel_workflow(session, get_agent_manager(), workflow_id)
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return await _load(session, workflow_id)


@router.post("/{workflow_id}/approve", response_model=WorkflowRead)
async def approve_workflow_step(
    workflow_id: int,
    body: WorkflowApprovalRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
) -> Workflow:
    """Clear a human approval checkpoint so the pipeline carries on."""
    await _require_enabled(session)
    workflow = await workflow_svc.approve_step(
        session, get_agent_manager(), workflow_id, note=body.note if body else None
    )
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return await _load(session, workflow_id)


@router.post("/{workflow_id}/reject", response_model=WorkflowRead)
async def reject_workflow_step(
    workflow_id: int,
    body: WorkflowApprovalRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
) -> Workflow:
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
    return await _load(session, workflow_id)


@router.post("/{workflow_id}/archive", response_model=WorkflowRead)
async def archive_workflow(
    workflow_id: int, session: AsyncSession = Depends(get_session)
) -> Workflow:
    workflow = await _load(session, workflow_id)
    workflow.archived_at = _now()
    await session.commit()
    await publish_workflow_changed(workflow_id)
    return await _load(session, workflow_id)


@router.post("/{workflow_id}/unarchive", response_model=WorkflowRead)
async def unarchive_workflow(
    workflow_id: int, session: AsyncSession = Depends(get_session)
) -> Workflow:
    workflow = await _load(session, workflow_id)
    workflow.archived_at = None
    await session.commit()
    await publish_workflow_changed(workflow_id)
    return await _load(session, workflow_id)


# --- Scheduling ------------------------------------------------------------


@router.put("/{workflow_id}/schedule", response_model=WorkflowRead)
async def update_schedule(
    workflow_id: int,
    payload: WorkflowScheduleUpdate,
    session: AsyncSession = Depends(get_session),
) -> Workflow:
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
    return await _load(session, workflow_id)


# --- Webhook trigger -------------------------------------------------------


@router.post("/{workflow_id}/webhook", response_model=WorkflowRead)
async def mint_webhook(workflow_id: int, session: AsyncSession = Depends(get_session)) -> Workflow:
    workflow = await _load(session, workflow_id)
    workflow.webhook_token = Workflow.mint_webhook_token()
    await session.commit()
    await publish_workflow_changed(workflow_id)
    return await _load(session, workflow_id)


@router.delete("/{workflow_id}/webhook", response_model=WorkflowRead)
async def revoke_webhook(
    workflow_id: int, session: AsyncSession = Depends(get_session)
) -> Workflow:
    workflow = await _load(session, workflow_id)
    workflow.webhook_token = None
    await session.commit()
    await publish_workflow_changed(workflow_id)
    return await _load(session, workflow_id)


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
