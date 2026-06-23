"""Agents router — manage Copilot SDK agent sessions (Agents mode).

Thin HTTP surface over :class:`AgentManager`: rows are persisted here, the
long-running runtime work is delegated to the manager. Live state and history
are streamed via the shared event bus (``agent.changed``) and read back through
``GET /api/agents/{id}/events``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models import AgentSession, Chat, Topic
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
from precursor.backend.services.agents import runtime
from precursor.backend.services.agents.manager import get_agent_manager
from precursor.backend.services.app_settings import resolve_agents_enabled
from precursor.backend.services.events import publish_agent_changed

router = APIRouter(prefix="/api/agents", tags=["agents"])


async def _require_runtime(session: AsyncSession) -> None:
    """Reject the request unless Agents mode is enabled *and* usable."""
    if not await resolve_agents_enabled(session):
        raise HTTPException(status.HTTP_409_CONFLICT, "Agents mode is disabled")
    ok, detail = runtime.agents_available()
    if not ok:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Agents runtime unavailable: {detail}")


async def _get_or_404(session: AsyncSession, agent_id: int) -> AgentSession:
    agent = await session.get(AgentSession, agent_id)
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
    stmt = select(AgentSession).order_by(AgentSession.created_at.desc())
    if topic_id is not None:
        stmt = stmt.where(AgentSession.topic_id == topic_id)
    if chat_id is not None:
        stmt = stmt.where(AgentSession.chat_id == chat_id)
    result = await session.execute(stmt)
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
async def get_agent(agent_id: int, session: AsyncSession = Depends(get_session)) -> AgentSession:
    return await _get_or_404(session, agent_id)


@router.get("/{agent_id}/events", response_model=list[AgentEvent])
async def get_agent_events(
    agent_id: int, session: AsyncSession = Depends(get_session)
) -> list[AgentEvent]:
    await _get_or_404(session, agent_id)
    return await get_agent_manager().get_events(agent_id)


@router.post("/{agent_id}/send", response_model=AgentSessionRead)
async def send_to_agent(
    agent_id: int,
    payload: AgentSendRequest,
    session: AsyncSession = Depends(get_session),
) -> AgentSession:
    await _require_runtime(session)
    agent = await _get_or_404(session, agent_id)
    mgr = get_agent_manager()
    mgr.enqueue(mgr.send_message(agent_id, payload.message))
    return agent


@router.post("/{agent_id}/cancel", response_model=AgentSessionRead)
async def cancel_agent(agent_id: int, session: AsyncSession = Depends(get_session)) -> AgentSession:
    agent = await _get_or_404(session, agent_id)
    await get_agent_manager().cancel(agent_id)
    await session.refresh(agent)
    return agent


@router.post("/{agent_id}/permission", response_model=AgentSessionRead)
async def resolve_permission(
    agent_id: int,
    payload: AgentPermissionDecision,
    session: AsyncSession = Depends(get_session),
) -> AgentSession:
    agent = await _get_or_404(session, agent_id)
    matched = await get_agent_manager().resolve_permission(
        agent_id, payload.request_id, payload.decision
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
    agent_id: int,
    payload: AgentUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> AgentSession:
    """Rename an agent session."""
    agent = await _get_or_404(session, agent_id)
    agent.title = payload.title.strip()[:200] or agent.title
    await session.commit()
    await session.refresh(agent)
    await publish_agent_changed(
        agent_session_id=agent.id, topic_id=agent.topic_id, chat_id=agent.chat_id
    )
    return agent


@router.patch("/{agent_id}/link", response_model=AgentSessionRead)
async def link_agent(
    agent_id: int,
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
        await get_agent_manager().teardown_session(agent_id)
    await publish_agent_changed(
        agent_session_id=agent.id, topic_id=agent.topic_id, chat_id=agent.chat_id
    )
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: int, session: AsyncSession = Depends(get_session)) -> None:
    agent = await _get_or_404(session, agent_id)
    topic_id, chat_id = agent.topic_id, agent.chat_id
    await get_agent_manager().teardown_session(agent_id)
    await session.delete(agent)
    await session.commit()
    await publish_agent_changed(agent_session_id=agent_id, topic_id=topic_id, chat_id=chat_id)
