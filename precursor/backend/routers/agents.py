"""Agents router — manage Copilot SDK agent sessions (Agents mode).

Thin HTTP surface over :class:`AgentManager`: rows are persisted here, the
long-running runtime work is delegated to the manager. Live state and history
are streamed via the shared event bus (``agent.changed``) and read back through
``GET /api/agents/{id}/events``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import get_settings
from precursor.backend.db import get_session
from precursor.backend.models import (
    AgentArtifact,
    AgentBlueprint,
    AgentEventRecord,
    AgentRun,
    AgentSchedule,
    AgentSession,
    AgentState,
    AgentTrigger,
    Chat,
    Topic,
    Workflow,
    WorkflowStep,
)
from precursor.backend.schemas.agent import (
    AgentArtifactCreate,
    AgentArtifactRead,
    AgentBlueprintCreate,
    AgentBlueprintInstantiate,
    AgentBlueprintRead,
    AgentBlueprintUpdate,
    AgentEvent,
    AgentInboxItem,
    AgentLinkRequest,
    AgentMetrics,
    AgentModelInfo,
    AgentPendingPermission,
    AgentPermissionDecision,
    AgentPermissionGrant,
    AgentRunRead,
    AgentSendRequest,
    AgentSessionCreate,
    AgentSessionRead,
    AgentStatusCount,
    AgentTriggerCreate,
    AgentTriggerRead,
    AgentUpdateRequest,
)
from precursor.backend.schemas.agent_schedule import (
    AgentScheduleCreate,
    AgentScheduleRead,
    AgentScheduleUpdate,
)
from precursor.backend.schemas.agent_state import (
    AgentStateRead,
    AgentStateWrite,
)
from precursor.backend.schemas.workflow import WorkflowSummary
from precursor.backend.services import agent_state as agent_state_service
from precursor.backend.services.agents import fleet, runtime
from precursor.backend.services.agents.manager import get_agent_manager, parse_agent_command
from precursor.backend.services.app_settings import resolve_agents_enabled
from precursor.backend.services.events import publish_agent_changed, publish_read_changed
from precursor.backend.services.schedule_timing import compute_next_run
from precursor.backend.services.scheduler import get_scheduler

router = APIRouter(prefix="/api/agents", tags=["agents"])


async def _require_runtime(session: AsyncSession) -> None:
    """Reject the request unless Agents mode is enabled *and* usable."""
    if not await resolve_agents_enabled(session):
        raise HTTPException(status.HTTP_409_CONFLICT, "Agents mode is disabled")
    ok, detail = runtime.agents_available()
    if not ok:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Agents runtime unavailable: {detail}")


async def _get_or_404(session: AsyncSession, agent_ref: str) -> AgentSession:
    """Resolve an agent by its public UUID (``public_id``) or, as a fallback,
    its legacy integer id. Deep links and the ``/agent`` command use the UUID;
    older bookmarks may still carry the integer id."""
    agent: AgentSession | None = None
    if agent_ref.isdigit():
        agent = await session.get(AgentSession, int(agent_ref))
    if agent is None:
        agent = (
            await session.execute(select(AgentSession).where(AgentSession.public_id == agent_ref))
        ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent session not found")
    return agent


async def _validate_container(
    session: AsyncSession, *, topic_id: int | None, chat_id: int | None
) -> None:
    if topic_id is not None and chat_id is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Link to a topic or a chat, not both")
    if topic_id is not None and await session.get(Topic, topic_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    if chat_id is not None and await session.get(Chat, chat_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")


# Marker used to recognise assistant replies in the archived (JSON) event blob.
# The timeline payload is an opaque ``AgentEvent`` dump, but its compact JSON
# always carries ``"kind":"assistant_message"`` for a finished reply, so a LIKE
# match is a cheap, migration-free way to count them without a dedicated column.
_ASSISTANT_EVENT_MARKER = '%"kind":"assistant_message"%'


async def _unread_counts(session: AsyncSession, agent_ids: list[int]) -> dict[int, int]:
    """Assistant replies produced after each agent's ``last_read_at``.

    Mirrors the topic/chat unread badge: a session with ``last_read_at`` unset is
    treated as fully read (so background history never shows retroactively), and
    only assistant replies — not intermediate tool/reasoning steps — count.
    """
    if not agent_ids:
        return {}
    result = await session.execute(
        select(AgentEventRecord.agent_session_id, func.count(AgentEventRecord.id))
        .join(AgentSession, AgentSession.id == AgentEventRecord.agent_session_id)
        .where(AgentSession.last_read_at.is_not(None))
        .where(AgentEventRecord.created_at > AgentSession.last_read_at)
        .where(AgentEventRecord.payload.like(_ASSISTANT_EVENT_MARKER))
        .where(AgentEventRecord.agent_session_id.in_(agent_ids))
        .group_by(AgentEventRecord.agent_session_id)
    )
    return {row[0]: row[1] for row in result.all()}


async def _workflow_counts(session: AsyncSession, agent_ids: list[int]) -> dict[int, int]:
    """Map agent id -> number of live workflows referencing it.

    One grouped query for the whole list rather than a request per card. Archived
    workflows don't count as live references, matching
    ``GET /api/agents/{id}/workflows``.
    """
    if not agent_ids:
        return {}
    result = await session.execute(
        select(WorkflowStep.agent_id, func.count(func.distinct(Workflow.id)))
        .join(Workflow, Workflow.id == WorkflowStep.workflow_id)
        .where(
            WorkflowStep.agent_id.in_(agent_ids),
            Workflow.archived_at.is_(None),
        )
        .group_by(WorkflowStep.agent_id)
    )
    return {row[0]: row[1] for row in result.all()}


async def _current_runs(session: AsyncSession, agents: list[AgentSession]) -> dict[int, AgentRun]:
    """The execution currently driving each agent, keyed by agent id.

    Fetched in one query rather than through a relationship: ``current_run``
    points back at the agent, so eager-loading it would recurse.
    """
    run_ids = [a.current_run_id for a in agents if a.current_run_id is not None]
    if not run_ids:
        return {}
    result = await session.execute(select(AgentRun).where(AgentRun.id.in_(run_ids)))
    return {run.agent_id: run for run in result.scalars().all()}


def _to_read(
    agent: AgentSession,
    unread: int,
    activity: dict[str, Any] | None = None,
    workflow_count: int = 0,
    current_run: AgentRun | None = None,
) -> AgentSessionRead:
    read = AgentSessionRead.model_validate(agent)
    read.unread_count = unread
    read.workflow_count = workflow_count
    if current_run is not None:
        read.current_run = AgentRunRead.model_validate(current_run)
    if activity:
        read.active_tool = activity.get("active_tool")
        read.active_tool_count = activity.get("active_tool_count", 0)
        read.active_narration = activity.get("active_narration")
        pending = activity.get("pending_permission")
        if pending:
            read.pending_permission = AgentPendingPermission.model_validate(pending)
    return read


@router.get("/models", response_model=list[AgentModelInfo])
async def list_agent_models(
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, str]]:
    """Available runtime models for the default-model picker (empty if down)."""
    return await get_agent_manager().list_models()


@router.get("/permissions", response_model=list[AgentPermissionGrant])
async def list_agent_permissions() -> list[dict[str, Any]]:
    """Recap of active "approve for session" grants (for the Settings panel)."""
    return await get_agent_manager().list_permissions()


@router.post("/permissions/reset")
async def reset_agent_permissions() -> dict[str, int]:
    """Revoke all session grants by resetting live sessions. Security control."""
    cleared = await get_agent_manager().reset_permissions()
    return {"cleared": cleared}


# --------------------------------------------------------------- observability
#
# Fleet-wide rollups for the dashboard header and the unified "blocked inbox".
# Declared before the ``/{agent_id}`` routes so the literal paths win the match.


async def _agent_spend(session: AsyncSession, agent_ids: list[int]) -> dict[int, int]:
    """Lifetime tokens per agent, summed over its runs.

    ``AgentSession.total_*`` is a write-through mirror of the agent's *current*
    run, so it under-reports any agent that has run more than once. Cumulative
    spend has to come from ``agent_runs`` — which the migration seeded with a
    synthetic run carrying pre-split history.
    """
    if not agent_ids:
        return {}
    rows = (
        await session.execute(
            select(
                AgentRun.agent_id,
                func.coalesce(
                    func.sum(AgentRun.total_input_tokens + AgentRun.total_output_tokens), 0
                ),
            )
            .where(AgentRun.agent_id.in_(agent_ids))
            .group_by(AgentRun.agent_id)
        )
    ).all()
    return {int(r[0]): int(r[1]) for r in rows}


def _is_budget_park(agent: AgentSession, spent: int) -> bool:
    """True when a blocked agent was parked by the token-budget governor.

    A budget park and a raised question both land on ``status="blocked"``; the
    distinguishing signal is that the governor only fires when the accrued spend
    has reached the configured ceiling. ``spent`` is the agent's cumulative spend
    across every run — the same total :meth:`AgentManager._enforce_budget` gates
    on, so the badge can't disagree with the governor that raised it.
    """
    return (
        agent.status == "blocked" and agent.token_budget is not None and spent >= agent.token_budget
    )


@router.get("/metrics", response_model=AgentMetrics)
async def get_agent_metrics(session: AsyncSession = Depends(get_session)) -> AgentMetrics:
    """Status counts + token totals + concurrency headroom for the header."""
    rows = (
        await session.execute(
            select(AgentSession.status, func.count(AgentSession.id))
            .where(AgentSession.archived_at.is_(None))
            .where(AgentSession.inline.is_(False))
            .group_by(AgentSession.status)
        )
    ).all()
    by_status = [AgentStatusCount(status=r[0], count=int(r[1])) for r in rows]
    counts = {r[0]: int(r[1]) for r in rows}
    # Tokens come from the runs: the agent's own counters mirror its *current*
    # run only, so summing those would report just the latest turn of each agent
    # as the fleet's lifetime spend.
    totals = (
        await session.execute(
            select(
                func.coalesce(func.sum(AgentRun.total_input_tokens), 0),
                func.coalesce(func.sum(AgentRun.total_output_tokens), 0),
            )
            .select_from(AgentRun)
            .join(AgentSession, AgentRun.agent_id == AgentSession.id)
            .where(AgentSession.archived_at.is_(None))
            .where(AgentSession.inline.is_(False))
        )
    ).one()
    total_in = int(totals[0])
    total_out = int(totals[1])
    return AgentMetrics(
        total=sum(counts.values()),
        active=counts.get("running", 0) + counts.get("needs_approval", 0),
        waiting=counts.get("blocked", 0),
        completed=counts.get("completed", 0),
        failed=counts.get("failed", 0),
        by_status=by_status,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        running_now=await fleet.running_count(session),
        max_concurrent=get_settings().agents_max_concurrent,
    )


@router.get("/inbox", response_model=list[AgentInboxItem])
async def get_agent_inbox(session: AsyncSession = Depends(get_session)) -> list[AgentInboxItem]:
    """Everything waiting on a human: raised questions, permission gates, budget parks.

    Aggregates the persisted ``blocked``/``needs_approval`` rows and enriches
    ``needs_approval`` with the live parked permission (title + ``request_id``)
    from the manager so the UI can deep-link straight to the approval card.
    """
    agents = list(
        (
            await session.execute(
                select(AgentSession)
                .where(AgentSession.archived_at.is_(None))
                .where(AgentSession.status.in_(("blocked", "needs_approval")))
                .order_by(AgentSession.last_activity_at.desc().nullslast())
            )
        )
        .scalars()
        .all()
    )
    activity = get_agent_manager().live_activity([a.id for a in agents])
    spend = await _agent_spend(session, [a.id for a in agents])
    items: list[AgentInboxItem] = []
    for agent in agents:
        if agent.status == "needs_approval":
            pending = (activity.get(agent.id) or {}).get("pending_permission") or {}
            items.append(
                AgentInboxItem(
                    agent_id=agent.id,
                    title=agent.title,
                    kind="needs_approval",
                    detail=pending.get("title"),
                    request_id=pending.get("request_id"),
                    at=agent.last_activity_at,
                )
            )
        elif _is_budget_park(agent, spend.get(agent.id, 0)):
            items.append(
                AgentInboxItem(
                    agent_id=agent.id,
                    title=agent.title,
                    kind="budget",
                    detail=agent.blocked_question,
                    at=agent.last_activity_at,
                )
            )
        else:
            items.append(
                AgentInboxItem(
                    agent_id=agent.id,
                    title=agent.title,
                    kind="blocked",
                    detail=agent.blocked_question,
                    at=agent.last_activity_at,
                )
            )
    return items


# ------------------------------------------------------------------ blueprints


@router.get("/blueprints", response_model=list[AgentBlueprintRead])
async def list_blueprints(
    session: AsyncSession = Depends(get_session),
) -> list[AgentBlueprint]:
    result = await session.execute(select(AgentBlueprint).order_by(AgentBlueprint.name.asc()))
    return list(result.scalars().all())


@router.post("/blueprints", response_model=AgentBlueprintRead, status_code=status.HTTP_201_CREATED)
async def create_blueprint(
    payload: AgentBlueprintCreate,
    session: AsyncSession = Depends(get_session),
) -> AgentBlueprint:
    blueprint = AgentBlueprint(**payload.model_dump())
    session.add(blueprint)
    await session.commit()
    await session.refresh(blueprint)
    return blueprint


@router.patch("/blueprints/{blueprint_id}", response_model=AgentBlueprintRead)
async def update_blueprint(
    blueprint_id: int,
    payload: AgentBlueprintUpdate,
    session: AsyncSession = Depends(get_session),
) -> AgentBlueprint:
    blueprint = await session.get(AgentBlueprint, blueprint_id)
    if blueprint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Blueprint not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(blueprint, field, value)
    await session.commit()
    await session.refresh(blueprint)
    return blueprint


@router.delete("/blueprints/{blueprint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_blueprint(blueprint_id: int, session: AsyncSession = Depends(get_session)) -> None:
    blueprint = await session.get(AgentBlueprint, blueprint_id)
    if blueprint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Blueprint not found")
    await session.delete(blueprint)
    await session.commit()


@router.post(
    "/blueprints/{blueprint_id}/instantiate",
    response_model=AgentSessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def instantiate_blueprint(
    blueprint_id: int,
    payload: AgentBlueprintInstantiate,
    session: AsyncSession = Depends(get_session),
) -> AgentSession:
    """Spawn a fresh agent seeded from a blueprint (with optional overrides)."""
    await _require_runtime(session)
    blueprint = await session.get(AgentBlueprint, blueprint_id)
    if blueprint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Blueprint not found")
    await _validate_container(session, topic_id=payload.topic_id, chat_id=payload.chat_id)

    task_prompt = (payload.task or blueprint.task_prompt).strip()
    if not task_prompt:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Blueprint has no task to run")
    title = (payload.title or blueprint.name or task_prompt).strip()[:200] or "Agent task"
    return await _spawn_agent(
        session,
        title=title,
        task_prompt=task_prompt,
        model=blueprint.model,
        topic_id=payload.topic_id,
        chat_id=payload.chat_id,
        role_id=blueprint.role_id,
        autonomy_enabled=blueprint.autonomy_enabled,
        max_steps=blueprint.max_steps,
        approval_policy=blueprint.approval_policy,
        token_budget=blueprint.token_budget,
        max_retries=blueprint.max_retries,
        blueprint_id=blueprint.id,
        start=payload.start,
    )


# --------------------------------------------------------------------- webhook


@router.post("/hooks/{token}", response_model=AgentSessionRead)
async def fire_agent_webhook(
    token: str, session: AsyncSession = Depends(get_session)
) -> AgentSession:
    """Kick an agent from an external event. The URL token is the only credential.

    Resolves the (enabled) trigger by its secret slug, records the fire, and
    re-runs the target agent's task in the background. Mirrors how CI/GitHub
    webhooks are addressed; a bad or disabled token 404s to avoid leaking which
    tokens exist.
    """
    await _require_runtime(session)
    trigger = (
        await session.execute(
            select(AgentTrigger).where(AgentTrigger.token == token, AgentTrigger.enabled.is_(True))
        )
    ).scalar_one_or_none()
    if trigger is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown or disabled trigger")
    agent = await session.get(AgentSession, trigger.agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent session not found")
    trigger.last_fired_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(agent)
    mgr = get_agent_manager()
    mgr.enqueue(mgr.restart_with_task(agent.id))
    return agent


@router.get("", response_model=list[AgentSessionRead])
async def list_agents(
    topic_id: int | None = None,
    chat_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[AgentSessionRead]:
    # Inline agents are execution vessels owned by a workflow step, not units the
    # user manages, so they stay out of the roster. They are deliberately still
    # listed by ``/attention`` below: a blocked inline step must remain
    # discoverable and resolvable, or a workflow could wedge invisibly.
    stmt = (
        select(AgentSession)
        .where(AgentSession.archived_at.is_(None))
        .where(AgentSession.inline.is_(False))
        .order_by(AgentSession.created_at.desc())
    )
    if topic_id is not None:
        stmt = stmt.where(AgentSession.topic_id == topic_id)
    if chat_id is not None:
        stmt = stmt.where(AgentSession.chat_id == chat_id)
    result = await session.execute(stmt)
    agents = list(result.scalars().all())
    ids = [a.id for a in agents]
    unread = await _unread_counts(session, ids)
    workflows = await _workflow_counts(session, ids)
    activity = get_agent_manager().live_activity(ids)
    runs = await _current_runs(session, agents)
    return [
        _to_read(
            a,
            unread.get(a.id, 0),
            activity.get(a.id),
            workflows.get(a.id, 0),
            runs.get(a.id),
        )
        for a in agents
    ]


@router.get("/archived", response_model=list[AgentSessionRead])
async def list_archived_agents(
    session: AsyncSession = Depends(get_session),
) -> list[AgentSession]:
    result = await session.execute(
        select(AgentSession)
        .where(AgentSession.archived_at.is_not(None))
        .order_by(AgentSession.archived_at.desc())
    )
    return list(result.scalars().all())


async def _spawn_agent(
    session: AsyncSession,
    *,
    title: str,
    task_prompt: str,
    model: str | None,
    topic_id: int | None,
    chat_id: int | None,
    role_id: int | None,
    autonomy_enabled: bool,
    max_steps: int,
    approval_policy: str | None,
    token_budget: int | None,
    max_retries: int,
    blueprint_id: int | None,
    start: bool,
) -> AgentSession:
    """Persist a new agent row and launch it when ``start``.

    ``start=False`` parks the agent in the ``waiting`` state instead of ``pending``:
    it is armed but idle until a trigger fires (a webhook or a manual "Start now").
    A ``waiting`` agent is never swept up automatically — only its explicit trigger
    launches it.
    """
    agent = AgentSession(
        title=title,
        task_prompt=task_prompt,
        model=model,
        topic_id=topic_id,
        chat_id=chat_id,
        role_id=role_id,
        autonomy_enabled=autonomy_enabled,
        max_steps=max_steps,
        approval_policy=approval_policy,
        token_budget=token_budget,
        max_retries=max_retries,
        blueprint_id=blueprint_id,
        status="pending" if start else "waiting",
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)

    await publish_agent_changed(
        agent_session_id=agent.id, topic_id=agent.topic_id, chat_id=agent.chat_id
    )
    if start:
        mgr = get_agent_manager()
        mgr.enqueue(mgr.start_task(agent.id))
    return agent


@router.post("", response_model=AgentSessionRead, status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: AgentSessionCreate,
    session: AsyncSession = Depends(get_session),
) -> AgentSession:
    await _require_runtime(session)
    await _validate_container(session, topic_id=payload.topic_id, chat_id=payload.chat_id)

    # A blueprint seeds the defaults; explicit payload fields still win over it.
    blueprint: AgentBlueprint | None = None
    if payload.blueprint_id is not None:
        blueprint = await session.get(AgentBlueprint, payload.blueprint_id)
        if blueprint is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Blueprint not found")

    task_prompt = payload.task or (blueprint.task_prompt if blueprint else "")
    if not task_prompt.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Agent task is required")

    title = (payload.title or task_prompt).strip()[:200] or "Agent task"
    return await _spawn_agent(
        session,
        title=title,
        task_prompt=task_prompt,
        model=payload.model or (blueprint.model if blueprint else None),
        topic_id=payload.topic_id,
        chat_id=payload.chat_id,
        role_id=payload.role_id
        if payload.role_id is not None
        else (blueprint.role_id if blueprint else None),
        autonomy_enabled=payload.autonomy_enabled
        or (blueprint.autonomy_enabled if blueprint else False),
        max_steps=payload.max_steps,
        approval_policy=payload.approval_policy
        or (blueprint.approval_policy if blueprint else None),
        token_budget=payload.token_budget
        if payload.token_budget is not None
        else (blueprint.token_budget if blueprint else None),
        max_retries=payload.max_retries or (blueprint.max_retries if blueprint else 0),
        blueprint_id=payload.blueprint_id,
        start=payload.start,
    )


@router.get("/{agent_id}/workflows", response_model=list[WorkflowSummary])
async def list_agent_workflows(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[WorkflowSummary]:
    """Which workflows reference this agent.

    Agents are shared and reusable, so before editing or deleting one it matters
    whether it is wired into a pipeline — and which. Archived workflows are left
    out; a private inline vessel never appears here because it is not listed in
    the Agents section to begin with.
    """
    agent = await _get_or_404(session, agent_id)
    result = await session.execute(
        select(Workflow)
        .join(WorkflowStep, WorkflowStep.workflow_id == Workflow.id)
        .where(WorkflowStep.agent_id == agent.id, Workflow.archived_at.is_(None))
        .order_by(Workflow.name)
    )
    return [WorkflowSummary.model_validate(w) for w in result.scalars().unique().all()]


@router.get("/{agent_id}", response_model=AgentSessionRead)
async def get_agent(
    agent_id: str, session: AsyncSession = Depends(get_session)
) -> AgentSessionRead:
    agent = await _get_or_404(session, agent_id)
    unread = await _unread_counts(session, [agent.id])
    workflows = await _workflow_counts(session, [agent.id])
    activity = get_agent_manager().live_activity([agent.id])
    runs = await _current_runs(session, [agent])
    return _to_read(
        agent,
        unread.get(agent.id, 0),
        activity.get(agent.id),
        workflows.get(agent.id, 0),
        runs.get(agent.id),
    )


@router.get("/{agent_id}/events", response_model=list[AgentEvent])
async def get_agent_events(
    agent_id: str, session: AsyncSession = Depends(get_session)
) -> list[AgentEvent]:
    agent = await _get_or_404(session, agent_id)
    return await get_agent_manager().get_events(agent.id)


@router.get("/{agent_id}/runs", response_model=list[AgentRunRead])
async def list_agent_runs(
    agent_id: str,
    limit: int = 50,
    workflow_run_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[AgentRun]:
    """This agent's execution history, newest first.

    An agent is a reusable definition, so "what did it do, driven by what, and
    what did that cost" is a per-run question. Filter by ``workflow_run_id`` to
    see only the executions one pipeline attempt drove.
    """
    agent = await _get_or_404(session, agent_id)
    stmt = select(AgentRun).where(AgentRun.agent_id == agent.id)
    if workflow_run_id is not None:
        stmt = stmt.where(AgentRun.workflow_run_id == workflow_run_id)
    result = await session.execute(stmt.order_by(AgentRun.id.desc()).limit(max(1, min(limit, 200))))
    return list(result.scalars().all())


@router.get("/{agent_id}/runs/{run_id}", response_model=AgentRunRead)
async def get_agent_run(
    agent_id: str, run_id: int, session: AsyncSession = Depends(get_session)
) -> AgentRun:
    agent = await _get_or_404(session, agent_id)
    run = await session.get(AgentRun, run_id)
    if run is None or run.agent_id != agent.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent run not found")
    return run


@router.post("/{agent_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_agent_read(agent_id: str, session: AsyncSession = Depends(get_session)) -> None:
    """Mark an agent session as fully read (clears its unread badge).

    Mirrors the chat/topic read endpoints: a plain state write with no event
    published, so marking read never echoes back as an ``agent.changed`` update.
    """
    agent = await _get_or_404(session, agent_id)
    agent.last_read_at = datetime.now(UTC)
    await session.commit()
    # Let other tabs clear this agent's badge/counter in real time.
    await publish_read_changed(agent_session_id=agent.id)


@router.post("/{agent_id}/unread", status_code=status.HTTP_204_NO_CONTENT)
async def mark_agent_unread(agent_id: str, session: AsyncSession = Depends(get_session)) -> None:
    agent = await _get_or_404(session, agent_id)
    latest = await session.scalar(
        select(AgentEventRecord.created_at)
        .where(AgentEventRecord.agent_session_id == agent.id)
        .where(AgentEventRecord.payload.like(_ASSISTANT_EVENT_MARKER))
        .order_by(AgentEventRecord.created_at.desc())
        .limit(1)
    )
    agent.last_read_at = (latest or datetime.now(UTC)) - timedelta(microseconds=1)
    await session.commit()
    await publish_read_changed(agent_session_id=agent.id)


@router.post("/{agent_id}/send", response_model=AgentSessionRead)
async def send_to_agent(
    agent_id: str,
    payload: AgentSendRequest,
    session: AsyncSession = Depends(get_session),
) -> AgentSession:
    await _require_runtime(session)
    agent = await _get_or_404(session, agent_id)
    mgr = get_agent_manager()
    # Slash commands are handled by the system (rename/clear/archive) instead of
    # being forwarded to the SDK as prompt text; any other command is rejected.
    command = parse_agent_command(payload.message)
    if command is not None:
        name, argument = command
        try:
            await mgr.run_command(agent.id, name, argument)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        await session.refresh(agent)
        return agent
    mgr.enqueue(mgr.send_message(agent.id, payload.message))
    return agent


@router.post("/{agent_id}/resume", response_model=AgentSessionRead)
async def resume_agent(agent_id: str, session: AsyncSession = Depends(get_session)) -> AgentSession:
    """Re-run the in-flight turn of an interrupted session.

    Resends the persisted ``active_prompt`` so the turn that was cut off (by a
    restart or the watchdog) completes and posts its result back. Rejected when
    there's nothing to resume.
    """
    await _require_runtime(session)
    agent = await _get_or_404(session, agent_id)
    if not (agent.active_prompt or "").strip():
        raise HTTPException(status.HTTP_409_CONFLICT, "Nothing to resume on this session")
    mgr = get_agent_manager()
    mgr.enqueue(mgr.resume(agent.id))
    return agent


@router.post("/{agent_id}/cancel", response_model=AgentSessionRead)
async def cancel_agent(agent_id: str, session: AsyncSession = Depends(get_session)) -> AgentSession:
    agent = await _get_or_404(session, agent_id)
    await get_agent_manager().cancel(agent.id)
    await session.refresh(agent)
    return agent


@router.post("/{agent_id}/start", response_model=AgentSessionRead)
async def start_agent(agent_id: str, session: AsyncSession = Depends(get_session)) -> AgentSession:
    """Manually launch a parked (or re-launch a finished) agent's objective.

    The counterpart to creating an agent with ``start=false``: it arms a
    ``waiting`` agent on demand. Also valid on a terminal agent (``completed`` /
    ``failed`` / ``cancelled``) to re-run it. A fresh objective run clears the
    previous run's artifacts (handled in the manager).
    """
    await _require_runtime(session)
    agent = await _get_or_404(session, agent_id)
    if agent.status in ("running", "needs_approval", "interrupted"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Agent is already active; nothing to start")
    mgr = get_agent_manager()
    mgr.enqueue(mgr.start_task(agent.id))
    return agent


@router.post("/{agent_id}/permission", response_model=AgentSessionRead)
async def resolve_permission(
    agent_id: str,
    payload: AgentPermissionDecision,
    session: AsyncSession = Depends(get_session),
) -> AgentSession:
    agent = await _get_or_404(session, agent_id)
    matched = await get_agent_manager().resolve_permission(
        agent.id, payload.request_id, payload.decision
    )
    if not matched:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No pending permission request")
    # The manager already flipped the agent back to ``running`` as it resolved;
    # reload so the response reflects that rather than the parked status.
    await session.refresh(agent)
    await publish_agent_changed(
        agent_session_id=agent.id, topic_id=agent.topic_id, chat_id=agent.chat_id
    )
    return agent


@router.patch("/{agent_id}", response_model=AgentSessionRead)
async def update_agent(
    agent_id: str,
    payload: AgentUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> AgentSession:
    """Rename an agent session and/or edit its task instructions.

    Editing the task can't take effect on a live session: the task prompt is
    delivered only once (``start_task``) and a resumed session keeps the old
    instructions in its history. So a *changed* task drops the cached SDK
    session to *prime* the new prompt for the next run — it is **not** replayed
    here. Saving is only a save; the caller launches the new objective
    explicitly via ``POST /{id}/start`` ("Save & run"), which clears the prior
    run's artifacts and re-runs. ``public_id`` is preserved so scheduled
    ``/agent <uuid>`` references keep resolving. Rejected mid-run to avoid
    racing an active turn.
    """
    agent = await _get_or_404(session, agent_id)

    if payload.title is not None:
        agent.title = payload.title.strip()[:200] or agent.title

    # Reassigning the role re-primes the agent: its persona lives in the SDK
    # session's system preamble, baked in once at (re)creation. Tearing the live
    # session down (below) forces the new persona to be injected on the next run.
    role_changed = False
    if "role_id" in payload.model_fields_set and payload.role_id != agent.role_id:
        agent.role_id = payload.role_id
        role_changed = True

    # Retuning the step budget is a plain field write — no session rebuild.
    if payload.max_steps is not None:
        agent.max_steps = payload.max_steps

    # Governance retune — plain field writes, effective on the next metered
    # round / retry. ``token_budget=None`` is meaningful (ungovern), so key off
    # ``model_fields_set`` to tell an explicit clear from an omitted field.
    if "token_budget" in payload.model_fields_set:
        agent.token_budget = payload.token_budget
        # Un-park a budget-blocked agent when its ceiling is raised or removed.
        # Gate on cumulative spend across every run, not the agent's mirrored
        # counters (which only carry the current run) — otherwise a raise to
        # just above the latest run's spend un-parks an agent whose lifetime
        # total is still over the new ceiling, and ``_enforce_budget`` simply
        # re-parks it on the next metered round.
        spent = (await _agent_spend(session, [agent.id])).get(agent.id, 0)
        if agent.status == "blocked" and (agent.token_budget is None or spent < agent.token_budget):
            agent.status = "idle"
            agent.blocked_question = None
    if payload.max_retries is not None:
        agent.max_retries = payload.max_retries

    # The approval policy is read fresh into the live session at the start of
    # every turn (not baked into the preamble), so changing it is a plain field
    # write that needs no teardown — it takes effect on the next turn. ``None``
    # means "inherit the global default", so we key off ``model_fields_set`` to
    # distinguish an explicit reset-to-inherit from an omitted field.
    if "approval_policy" in payload.model_fields_set:
        agent.approval_policy = payload.approval_policy

    # Toggling autonomy changes the system preamble (the autonomy protocol block
    # is injected only when enabled), which is baked in at session build time.
    # Tear the live session down so the new preamble takes on the next run.
    autonomy_changed = False
    if payload.autonomy_enabled is not None and payload.autonomy_enabled != agent.autonomy_enabled:
        agent.autonomy_enabled = payload.autonomy_enabled
        autonomy_changed = True

    # Capability toggles change what's baked into the session (tool servers,
    # memory/skills preamble), so a flip must rebuild it like a role change does.
    caps_changed = False
    for field in ("use_mcp", "use_skills", "use_memory"):
        value = getattr(payload, field)
        if value is not None and value != getattr(agent, field):
            setattr(agent, field, value)
            caps_changed = True

    task_changed = False
    if payload.task is not None:
        new_task = payload.task.strip()
        if new_task and new_task != agent.task_prompt:
            if agent.status in {"pending", "running", "needs_approval", "interrupted"}:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Stop the agent before editing its instructions",
                )
            await _require_runtime(session)
            agent.task_prompt = new_task
            task_changed = True

    await session.commit()
    await session.refresh(agent)

    # Editing the task (or role / autonomy / capabilities) only *primes* the
    # change: drop the cached SDK session so the new instructions / persona /
    # protocol / tool set are re-injected on the next run. Saving never launches
    # a turn on its own — the caller runs the new objective explicitly via
    # POST /{id}/start ("Save & run").
    if task_changed or role_changed or autonomy_changed or caps_changed:
        await get_agent_manager().teardown_session(agent.id)

    await publish_agent_changed(
        agent_session_id=agent.id, topic_id=agent.topic_id, chat_id=agent.chat_id
    )
    return agent


@router.patch("/{agent_id}/link", response_model=AgentSessionRead)
async def link_agent(
    agent_id: str,
    payload: AgentLinkRequest,
    session: AsyncSession = Depends(get_session),
) -> AgentSession:
    """Attach the session to a topic/chat, or detach it (both null)."""
    agent = await _get_or_404(session, agent_id)
    await _validate_container(session, topic_id=payload.topic_id, chat_id=payload.chat_id)
    topic_changed = agent.topic_id != payload.topic_id
    agent.topic_id = payload.topic_id
    agent.chat_id = payload.chat_id
    await session.commit()
    await session.refresh(agent)
    # Drop the live session so the bound-topic context is re-injected on next use.
    if topic_changed:
        await get_agent_manager().teardown_session(agent.id)
    await publish_agent_changed(
        agent_session_id=agent.id, topic_id=agent.topic_id, chat_id=agent.chat_id
    )
    return agent


@router.post("/{agent_id}/archive", response_model=AgentSessionRead)
async def archive_agent(
    agent_id: str, session: AsyncSession = Depends(get_session)
) -> AgentSession:
    """Hide the session from the active list (kept for history). Mirrors topics."""
    agent = await _get_or_404(session, agent_id)
    if agent.archived_at is None:
        agent.archived_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(agent)
        await publish_agent_changed(
            agent_session_id=agent.id, topic_id=agent.topic_id, chat_id=agent.chat_id
        )
    return agent


@router.post("/{agent_id}/unarchive", response_model=AgentSessionRead)
async def unarchive_agent(
    agent_id: str, session: AsyncSession = Depends(get_session)
) -> AgentSession:
    agent = await _get_or_404(session, agent_id)
    if agent.archived_at is not None:
        agent.archived_at = None
        await session.commit()
        await session.refresh(agent)
        await publish_agent_changed(
            agent_session_id=agent.id, topic_id=agent.topic_id, chat_id=agent.chat_id
        )
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str, session: AsyncSession = Depends(get_session)) -> None:
    agent = await _get_or_404(session, agent_id)
    aid, topic_id, chat_id = agent.id, agent.topic_id, agent.chat_id
    await get_agent_manager().teardown_session(aid, forget=True)
    await session.delete(agent)
    await session.commit()
    await publish_agent_changed(agent_session_id=aid, topic_id=topic_id, chat_id=chat_id)


# --------------------------------------------------------------------- schedule
#
# An agent session may carry a recurrence so it re-runs its task on a cadence,
# mirroring scheduled topics (see routers/schedules.py + services/scheduler.py).
# The schedule replays the agent's own ``task_prompt`` — there is no separate
# prompt — optionally from a fresh context (``clear_context``). The background
# scheduler executes due rows.


def _now() -> datetime:
    return datetime.now(UTC)


async def _get_schedule_or_404(session: AsyncSession, agent_id: int) -> AgentSchedule:
    result = await session.execute(
        select(AgentSchedule).where(AgentSchedule.agent_session_id == agent_id)
    )
    schedule = result.scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent schedule not found")
    return schedule


@router.get("/{agent_id}/schedule", response_model=AgentScheduleRead)
async def get_agent_schedule(
    agent_id: str, session: AsyncSession = Depends(get_session)
) -> AgentSchedule:
    agent = await _get_or_404(session, agent_id)
    return await _get_schedule_or_404(session, agent.id)


@router.post(
    "/{agent_id}/schedule",
    response_model=AgentScheduleRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_schedule(
    agent_id: str,
    payload: AgentScheduleCreate,
    session: AsyncSession = Depends(get_session),
) -> AgentSchedule:
    agent = await _get_or_404(session, agent_id)
    existing = await session.execute(
        select(AgentSchedule).where(AgentSchedule.agent_session_id == agent.id)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Agent already has a schedule")

    schedule = AgentSchedule(
        agent_session_id=agent.id,
        enabled=payload.enabled,
        interval_seconds=payload.interval_seconds,
        days_of_week=payload.days_of_week,
        run_at_minute=payload.run_at_minute,
        timezone=payload.timezone,
        clear_context=payload.clear_context,
        next_run_at=compute_next_run(
            _now(),
            payload.interval_seconds,
            payload.days_of_week,
            payload.run_at_minute,
            payload.timezone,
        )
        if payload.enabled
        else None,
        status="idle",
    )
    session.add(schedule)
    await session.commit()
    await session.refresh(schedule)
    await publish_agent_changed(
        agent_session_id=agent.id, topic_id=agent.topic_id, chat_id=agent.chat_id
    )
    return schedule


@router.patch("/{agent_id}/schedule", response_model=AgentScheduleRead)
async def update_agent_schedule(
    agent_id: str,
    payload: AgentScheduleUpdate,
    session: AsyncSession = Depends(get_session),
) -> AgentSchedule:
    agent = await _get_or_404(session, agent_id)
    schedule = await _get_schedule_or_404(session, agent.id)
    data = payload.model_dump(exclude_unset=True)

    if data.get("interval_seconds"):
        schedule.interval_seconds = data["interval_seconds"]
    if data.get("days_of_week"):
        schedule.days_of_week = data["days_of_week"]
    if data.get("timezone"):
        schedule.timezone = data["timezone"]
    if "clear_context" in data and data["clear_context"] is not None:
        schedule.clear_context = data["clear_context"]

    # run_at_minute is tri-state: omitted = unchanged, int = daily-at-time,
    # explicit null = back to interval mode.
    cadence_changed = (
        "interval_seconds" in data
        or "days_of_week" in data
        or "timezone" in data
        or "run_at_minute" in data
    )
    if "run_at_minute" in data:
        schedule.run_at_minute = data["run_at_minute"]

    # Re-anchor the next run from now whenever cadence/days/time changed.
    if cadence_changed and schedule.enabled:
        schedule.next_run_at = compute_next_run(
            _now(),
            schedule.interval_seconds,
            schedule.days_of_week,
            schedule.run_at_minute,
            schedule.timezone,
        )

    if "enabled" in data and data["enabled"] is not None:
        schedule.enabled = data["enabled"]
        if schedule.enabled and schedule.next_run_at is None:
            schedule.next_run_at = compute_next_run(
                _now(),
                schedule.interval_seconds,
                schedule.days_of_week,
                schedule.run_at_minute,
                schedule.timezone,
            )
        if not schedule.enabled:
            schedule.next_run_at = None

    await session.commit()
    await session.refresh(schedule)
    await publish_agent_changed(
        agent_session_id=agent.id, topic_id=agent.topic_id, chat_id=agent.chat_id
    )
    return schedule


@router.post("/{agent_id}/schedule/run", response_model=AgentScheduleRead)
async def run_agent_schedule_now(
    agent_id: str, session: AsyncSession = Depends(get_session)
) -> AgentSchedule:
    """Pull the next run forward so the ticker triggers the agent immediately."""
    agent = await _get_or_404(session, agent_id)
    schedule = await _get_schedule_or_404(session, agent.id)
    if schedule.status == "running":
        raise HTTPException(status.HTTP_409_CONFLICT, "Run already in progress")
    await session.execute(
        update(AgentSchedule)
        .where(AgentSchedule.agent_session_id == agent.id)
        .values(
            enabled=True,
            next_run_at=_now(),
            status="idle",
            lease_until=None,
            last_error=None,
        )
    )
    await session.commit()
    await session.refresh(schedule)
    await publish_agent_changed(
        agent_session_id=agent.id, topic_id=agent.topic_id, chat_id=agent.chat_id
    )
    # Nudge the scheduler so the run fires now instead of waiting for the next
    # poll tick (no-op if the scheduler is disabled).
    await get_scheduler().nudge()
    return schedule


@router.delete("/{agent_id}/schedule", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_schedule(
    agent_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    agent = await _get_or_404(session, agent_id)
    schedule = await _get_schedule_or_404(session, agent.id)
    await session.delete(schedule)
    await session.commit()
    await publish_agent_changed(
        agent_session_id=agent.id, topic_id=agent.topic_id, chat_id=agent.chat_id
    )


# ---------------------------------------------------- artifacts (the blackboard)


@router.get("/{agent_id}/artifacts", response_model=list[AgentArtifactRead])
async def list_agent_artifacts(
    agent_id: str,
    run_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[AgentArtifact]:
    """The agent's blackboard. Pass ``run_id`` to scope it to one execution —
    two workflows driving the same agent each publish to their own run."""
    agent = await _get_or_404(session, agent_id)
    stmt = select(AgentArtifact).where(AgentArtifact.agent_id == agent.id)
    if run_id is not None:
        stmt = stmt.where(AgentArtifact.agent_run_id == run_id)
    result = await session.execute(stmt.order_by(AgentArtifact.created_at.desc()))
    return list(result.scalars().all())


@router.post(
    "/{agent_id}/artifacts",
    response_model=AgentArtifactRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_artifact(
    agent_id: str,
    payload: AgentArtifactCreate,
    session: AsyncSession = Depends(get_session),
) -> AgentArtifact:
    """Publish an artifact to an agent's blackboard by hand (also done by the
    ``ARTIFACT:`` directive during a run). Downstream agents receive it as
    upstream context when they start."""
    agent = await _get_or_404(session, agent_id)
    artifact = AgentArtifact(
        agent_id=agent.id,
        # Hand-published artifacts join whatever the agent is executing now, so
        # a step that publishes mid-run sees them alongside its own.
        agent_run_id=agent.current_run_id,
        title=payload.title.strip()[:200],
        content=payload.content,
        kind=payload.kind,
        key=payload.key,
    )
    session.add(artifact)
    await session.commit()
    await session.refresh(artifact)
    await publish_agent_changed(
        agent_session_id=agent.id, topic_id=agent.topic_id, chat_id=agent.chat_id
    )
    return artifact


async def _get_artifact_or_404(
    session: AsyncSession, agent_id: str, artifact_id: int
) -> AgentArtifact:
    """Resolve one artifact scoped to its owning agent (404 on either miss)."""
    agent = await _get_or_404(session, agent_id)
    artifact = await session.get(AgentArtifact, artifact_id)
    if artifact is None or artifact.agent_id != agent.id:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact


@router.get("/{agent_id}/artifacts/{artifact_id}", response_model=AgentArtifactRead)
async def get_agent_artifact(
    agent_id: str,
    artifact_id: int,
    session: AsyncSession = Depends(get_session),
) -> AgentArtifact:
    """Fetch a single artifact by id. Backs the artifact permalink/viewer so a
    published output is addressable on its own, not only via the list."""
    return await _get_artifact_or_404(session, agent_id, artifact_id)


# Map an artifact's rendering ``kind`` to the content-type served by the raw
# endpoint. Unknown kinds fall back to plain text (``link`` never reaches here —
# it redirects to its URL instead).
_ARTIFACT_RAW_MEDIA = {
    "text": "text/plain; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
    "json": "application/json; charset=utf-8",
}


@router.get("/{agent_id}/artifacts/{artifact_id}/raw")
async def get_agent_artifact_raw(
    agent_id: str,
    artifact_id: int,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Serve an artifact's raw body with a kind-appropriate content-type so it
    can be linked to, downloaded, or consumed programmatically. A ``link``
    artifact (whose content *is* a URL) redirects to that URL instead."""
    artifact = await _get_artifact_or_404(session, agent_id, artifact_id)
    if artifact.kind == "link":
        target = (artifact.content or "").strip()
        if not target:
            raise HTTPException(status_code=404, detail="Artifact link is empty")
        return RedirectResponse(url=target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    media = _ARTIFACT_RAW_MEDIA.get(artifact.kind, "text/plain; charset=utf-8")
    return Response(content=artifact.content or "", media_type=media)


# ------------------------------------------------------------------- triggers


@router.get("/{agent_id}/triggers", response_model=list[AgentTriggerRead])
async def list_agent_triggers(
    agent_id: str, session: AsyncSession = Depends(get_session)
) -> list[AgentTrigger]:
    agent = await _get_or_404(session, agent_id)
    result = await session.execute(
        select(AgentTrigger)
        .where(AgentTrigger.agent_id == agent.id)
        .order_by(AgentTrigger.created_at.desc())
    )
    return list(result.scalars().all())


@router.post(
    "/{agent_id}/triggers",
    response_model=AgentTriggerRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_trigger(
    agent_id: str,
    payload: AgentTriggerCreate,
    session: AsyncSession = Depends(get_session),
) -> AgentTrigger:
    """Mint a webhook trigger for an agent. The returned ``token`` addresses the
    public ``POST /api/agents/hooks/{token}`` endpoint that fires it."""
    agent = await _get_or_404(session, agent_id)
    trigger = AgentTrigger(agent_id=agent.id, type=payload.type, enabled=payload.enabled)
    session.add(trigger)
    await session.commit()
    await session.refresh(trigger)
    await publish_agent_changed(
        agent_session_id=agent.id, topic_id=agent.topic_id, chat_id=agent.chat_id
    )
    return trigger


@router.delete("/{agent_id}/triggers/{trigger_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_trigger(
    agent_id: str,
    trigger_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    agent = await _get_or_404(session, agent_id)
    trigger = (
        await session.execute(
            select(AgentTrigger).where(
                AgentTrigger.id == trigger_id, AgentTrigger.agent_id == agent.id
            )
        )
    ).scalar_one_or_none()
    if trigger is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trigger not found")
    await session.delete(trigger)
    await session.commit()
    await publish_agent_changed(
        agent_session_id=agent.id, topic_id=agent.topic_id, chat_id=agent.chat_id
    )


# ------------------------------------------------- state (the private scratchpad)
#
# Distinct from artifacts above: artifacts are *published* deliverables and are
# cleared at the start of every fresh run, while state is private bookkeeping
# that deliberately survives re-runs. The UI exposes it so an operator can
# inspect a recurring agent's cursor — or reset one that has got stuck.


@router.get("/{agent_id}/state", response_model=list[AgentStateRead])
async def list_agent_state(
    agent_id: str, session: AsyncSession = Depends(get_session)
) -> list[AgentState]:
    agent = await _get_or_404(session, agent_id)
    return await agent_state_service.list_states(session, agent.id)


@router.put("/{agent_id}/state", response_model=AgentStateRead)
async def set_agent_state(
    agent_id: str,
    payload: AgentStateWrite,
    session: AsyncSession = Depends(get_session),
) -> AgentState:
    """Upsert one state entry (same semantics as the ``state_set`` MCP tool)."""
    agent = await _get_or_404(session, agent_id)
    try:
        state, _created = await agent_state_service.set_state(session, agent.id, payload)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return state


@router.delete("/{agent_id}/state/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_state(
    agent_id: str, key: str, session: AsyncSession = Depends(get_session)
) -> None:
    agent = await _get_or_404(session, agent_id)
    if not await agent_state_service.delete_state(session, agent.id, key.strip().lower()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "State key not found")


@router.delete("/{agent_id}/state", status_code=status.HTTP_204_NO_CONTENT)
async def clear_agent_state(agent_id: str, session: AsyncSession = Depends(get_session)) -> None:
    """Wipe the whole scratchpad — the operator's "start from scratch" lever for
    an agent whose saved cursor has gone bad."""
    agent = await _get_or_404(session, agent_id)
    await agent_state_service.clear_states(session, agent.id)
