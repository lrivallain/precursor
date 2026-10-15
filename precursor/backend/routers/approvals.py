"""Pending tool approvals of a topic or chat, and resuming a parked turn.

A turn that hit an approval gate stops and persists its pending calls (see
``services/approvals.py``). These routes list them, record the user's answers
and — once nothing is left waiting — stream the rest of the turn.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from precursor.backend.db import get_session
from precursor.backend.models import Chat, Topic
from precursor.backend.routers.deps import get_chat_or_404, get_topic_or_404
from precursor.backend.schemas import (
    ApprovalDecisionResult,
    ToolApprovalDecision,
    ToolApprovalRead,
)
from precursor.backend.services import approvals as approval_service
from precursor.backend.services import skills as skills_service
from precursor.backend.services.conversation_turn import (
    ResumeConflict,
    check_resumable,
    resolve_turn_settings,
    resume_turn,
)
from precursor.backend.services.events import publish_approval_changed
from precursor.backend.services.llm.base import ChatMessage
from precursor.backend.services.turn_engine import (
    ContainerKind,
    apply_chat_system_prompt,
    build_chat_system_context,
    build_system_context,
    container_message_kwargs,
)

topic_router = APIRouter(prefix="/api/topics/{topic_id}/approvals", tags=["approvals"])
chat_router = APIRouter(prefix="/api/chats/{chat_id}/approvals", tags=["approvals"])


async def _list(
    session: AsyncSession, kind: ContainerKind, container_id: int
) -> list[ToolApprovalRead]:
    rows = await approval_service.list_approvals(session, kind, container_id)
    return [ToolApprovalRead.model_validate(r) for r in rows]


async def _decide(
    session: AsyncSession,
    kind: ContainerKind,
    container_id: int,
    approval_id: int,
    payload: ToolApprovalDecision,
) -> ApprovalDecisionResult:
    row = await approval_service.decide(
        session,
        kind,
        container_id,
        approval_id,
        approve=payload.approve,
        scope=payload.scope,
        arg_match=payload.arg_match,
        feedback=payload.feedback,
    )
    await publish_approval_changed(**container_message_kwargs(kind, container_id))
    pending = (await approval_service.pending_counts(session, kind, [container_id])).get(
        container_id, 0
    )
    return ApprovalDecisionResult(approval=ToolApprovalRead.model_validate(row), pending=pending)


async def _resume(
    session: AsyncSession,
    kind: ContainerKind,
    container_id: int,
    *,
    system_prompt: str,
    finish_history: Callable[[list[ChatMessage]], list[ChatMessage]] | None = None,
) -> EventSourceResponse:
    try:
        decided = await check_resumable(session, kind, container_id)
    except ResumeConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    settings = await resolve_turn_settings(
        session,
        topic_id=container_id if kind == "topic" else None,
        chat_id=container_id if kind == "chat" else None,
    )
    return EventSourceResponse(
        resume_turn(
            kind,
            container_id,
            system_prompt=system_prompt,
            decided=decided,
            settings=settings,
            finish_history=finish_history,
        )
    )


@topic_router.get("", response_model=list[ToolApprovalRead])
async def list_topic_approvals(
    topic_id: int, session: AsyncSession = Depends(get_session)
) -> list[ToolApprovalRead]:
    await get_topic_or_404(topic_id, session)
    return await _list(session, "topic", topic_id)


@topic_router.post("/resume")
async def resume_topic_turn(
    topic_id: int, session: AsyncSession = Depends(get_session)
) -> EventSourceResponse:
    topic: Topic = await get_topic_or_404(topic_id, session)
    return await _resume(
        session, "topic", topic_id, system_prompt=await build_system_context(session, topic)
    )


@topic_router.post("/{approval_id}", response_model=ApprovalDecisionResult)
async def decide_topic_approval(
    topic_id: int,
    approval_id: int,
    payload: ToolApprovalDecision,
    session: AsyncSession = Depends(get_session),
) -> ApprovalDecisionResult:
    await get_topic_or_404(topic_id, session)
    return await _decide(session, "topic", topic_id, approval_id, payload)


@chat_router.get("", response_model=list[ToolApprovalRead])
async def list_chat_approvals(
    chat_id: int, session: AsyncSession = Depends(get_session)
) -> list[ToolApprovalRead]:
    await get_chat_or_404(chat_id, session)
    return await _list(session, "chat", chat_id)


@chat_router.post("/resume")
async def resume_chat_turn(
    chat_id: int, session: AsyncSession = Depends(get_session)
) -> EventSourceResponse:
    chat: Chat = await get_chat_or_404(chat_id, session)
    description = await skills_service.expand_references(session, chat.description or "")
    return await _resume(
        session,
        "chat",
        chat_id,
        system_prompt=await build_chat_system_context(session, chat),
        finish_history=lambda history: apply_chat_system_prompt(
            chat, history, description=description
        ),
    )


@chat_router.post("/{approval_id}", response_model=ApprovalDecisionResult)
async def decide_chat_approval(
    chat_id: int,
    approval_id: int,
    payload: ToolApprovalDecision,
    session: AsyncSession = Depends(get_session),
) -> ApprovalDecisionResult:
    await get_chat_or_404(chat_id, session)
    return await _decide(session, "chat", chat_id, approval_id, payload)
