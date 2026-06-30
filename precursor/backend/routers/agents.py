"""Agents router — manage Copilot SDK agent sessions (Agents mode).

Thin HTTP surface over :class:`AgentManager`: rows are persisted here, the
long-running runtime work is delegated to the manager. Live state and history
are streamed via the shared event bus (``agent.changed``) and read back through
``GET /api/agents/{id}/events``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models import AgentSchedule, AgentSession, Chat, Topic
from precursor.backend.schemas.agent import (
    AgentEvent,
    AgentLinkRequest,
    AgentModelInfo,
    AgentPermissionDecision,
    AgentPermissionGrant,
    AgentSendRequest,
    AgentSessionCreate,
    AgentSessionRead,
    AgentUpdateRequest,
)
from precursor.backend.schemas.agent_schedule import (
    AgentScheduleCreate,
    AgentScheduleRead,
    AgentScheduleUpdate,
)
from precursor.backend.services.agents import runtime
from precursor.backend.services.agents.manager import get_agent_manager, parse_agent_command
from precursor.backend.services.app_settings import resolve_agents_enabled
from precursor.backend.services.events import publish_agent_changed
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
    """Resolve an agent by its public UUID (``copilot_session_id``) or, as a
    fallback, its legacy integer id. Deep links and the ``/agent`` command use
    the UUID; older bookmarks may still carry the integer id."""
    agent: AgentSession | None = None
    if agent_ref.isdigit():
        agent = await session.get(AgentSession, int(agent_ref))
    if agent is None:
        agent = (
            await session.execute(
                select(AgentSession).where(AgentSession.copilot_session_id == agent_ref)
            )
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


@router.get("/models", response_model=list[AgentModelInfo])
async def list_agent_models(
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, str]]:
    """Available runtime models for the default-model picker (empty if down)."""
    return await get_agent_manager().list_models()


@router.get("/permissions", response_model=list[AgentPermissionGrant])
async def list_agent_permissions() -> list[dict[str, Any]]:
    """Recap of active "approve for session" grants (for the Settings panel)."""
    return get_agent_manager().list_permissions()


@router.post("/permissions/reset")
async def reset_agent_permissions() -> dict[str, int]:
    """Revoke all session grants by resetting live sessions. Security control."""
    cleared = await get_agent_manager().reset_permissions()
    return {"cleared": cleared}


@router.get("", response_model=list[AgentSessionRead])
async def list_agents(
    topic_id: int | None = None,
    chat_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[AgentSession]:
    stmt = (
        select(AgentSession)
        .where(AgentSession.archived_at.is_(None))
        .order_by(AgentSession.created_at.desc())
    )
    if topic_id is not None:
        stmt = stmt.where(AgentSession.topic_id == topic_id)
    if chat_id is not None:
        stmt = stmt.where(AgentSession.chat_id == chat_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


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


@router.post("", response_model=AgentSessionRead, status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: AgentSessionCreate,
    session: AsyncSession = Depends(get_session),
) -> AgentSession:
    await _require_runtime(session)
    await _validate_container(session, topic_id=payload.topic_id, chat_id=payload.chat_id)

    title = (payload.title or payload.task).strip()[:200] or "Agent task"
    agent = AgentSession(
        title=title,
        task_prompt=payload.task,
        model=payload.model,
        topic_id=payload.topic_id,
        chat_id=payload.chat_id,
        status="pending",
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)

    await publish_agent_changed(
        agent_session_id=agent.id, topic_id=agent.topic_id, chat_id=agent.chat_id
    )
    # Kick off the task in the background; the manager streams progress via the bus.
    mgr = get_agent_manager()
    mgr.enqueue(mgr.start_task(agent.id))
    return agent


@router.get("/{agent_id}", response_model=AgentSessionRead)
async def get_agent(agent_id: str, session: AsyncSession = Depends(get_session)) -> AgentSession:
    return await _get_or_404(session, agent_id)


@router.get("/{agent_id}/events", response_model=list[AgentEvent])
async def get_agent_events(
    agent_id: str, session: AsyncSession = Depends(get_session)
) -> list[AgentEvent]:
    agent = await _get_or_404(session, agent_id)
    return await get_agent_manager().get_events(agent.id)


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
    # The session resumes; reflect that it's working again.
    agent.status = "running"
    await session.commit()
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
    instructions in its history. So a *changed* task re-establishes the SDK
    session — replaying the new prompt — while preserving ``copilot_session_id``
    so scheduled ``/agent <uuid>`` references keep resolving. Rejected mid-run to
    avoid racing an active turn.
    """
    agent = await _get_or_404(session, agent_id)

    if payload.title is not None:
        agent.title = payload.title.strip()[:200] or agent.title

    restart = False
    if payload.task is not None:
        new_task = payload.task.strip()
        if new_task and new_task != agent.task_prompt:
            if agent.status in {"pending", "running", "needs_approval"}:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Stop the agent before editing its instructions",
                )
            await _require_runtime(session)
            agent.task_prompt = new_task
            restart = True

    await session.commit()
    await session.refresh(agent)

    if restart:
        mgr = get_agent_manager()
        mgr.enqueue(mgr.restart_with_task(agent.id))

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
