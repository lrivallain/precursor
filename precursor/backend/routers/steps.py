"""Step-by-step plans — a long ask worked as a tree of steps, then consolidated.

Thin HTTP surface over :mod:`precursor.backend.services.step_plans` (data and
rules) and :mod:`precursor.backend.services.step_plan_llm` (the model-driven
actions). Every mutation publishes ``step-plan.changed`` so other windows
refresh.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models.step_plan import AUTHOR_USER, AUTOMATION_AUTO
from precursor.backend.schemas.chat import ChatRead
from precursor.backend.schemas.message import MessageRead
from precursor.backend.schemas.step_plan import (
    ArtifactDraftResult,
    ConsolidateRequest,
    FinalDraftResult,
    FinalWrite,
    SendToTopicRequest,
    StepArtifactRevert,
    StepArtifactVersionRead,
    StepArtifactWrite,
    StepCreate,
    StepGenerateRequest,
    StepMove,
    StepPlanCreate,
    StepPlanItem,
    StepPlanRead,
    StepPlanReview,
    StepPlanUpdate,
    StepRead,
    StepStartRequest,
    StepStatusUpdate,
    StepUpdate,
    UseMessageRequest,
)
from precursor.backend.services import step_plan_llm as llm
from precursor.backend.services import step_plan_runner as runner
from precursor.backend.services import step_plans as svc
from precursor.backend.services.events import publish_step_plan_changed
from precursor.backend.services.side_chats import to_chat_read

router = APIRouter(prefix="/api/step-plans", tags=["step-plans"])
steps_router = APIRouter(prefix="/api/steps", tags=["step-plans"])


async def _plan(session: AsyncSession, plan_id: int) -> StepPlanRead:
    return await svc.to_plan_read(session, await svc.get_plan(session, plan_id))


async def _step(session: AsyncSession, step_id: int) -> StepRead:
    return await svc.read_step(session, await svc.get_step(session, step_id))


# --- plans --------------------------------------------------------------------


@router.get("", response_model=list[StepPlanItem])
async def list_plans(
    topic_id: int | None = None, session: AsyncSession = Depends(get_session)
) -> list[StepPlanItem]:
    return await svc.list_plans(session, topic_id)


@router.post("", response_model=StepPlanRead, status_code=status.HTTP_201_CREATED)
async def create_plan(
    payload: StepPlanCreate, session: AsyncSession = Depends(get_session)
) -> StepPlanRead:
    plan = await svc.create_plan(session, payload)
    if payload.propose:
        try:
            await llm.suggest_steps(session, plan.id, mode="initial")
        finally:
            # The plan exists either way; a failed proposal just leaves it empty.
            await publish_step_plan_changed(plan.id)
    await publish_step_plan_changed(plan.id)
    return await _plan(session, plan.id)


@router.get("/{plan_id}", response_model=StepPlanRead)
async def get_plan(plan_id: int, session: AsyncSession = Depends(get_session)) -> StepPlanRead:
    return await _plan(session, plan_id)


@router.patch("/{plan_id}", response_model=StepPlanRead)
async def update_plan(
    plan_id: int, payload: StepPlanUpdate, session: AsyncSession = Depends(get_session)
) -> StepPlanRead:
    await svc.update_plan(session, plan_id, payload)
    await publish_step_plan_changed(plan_id)
    return await _plan(session, plan_id)


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(plan_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    await svc.delete_plan(session, plan_id)
    await publish_step_plan_changed(plan_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{plan_id}/propose", response_model=StepPlanRead)
async def propose_steps(
    plan_id: int,
    payload: StepGenerateRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> StepPlanRead:
    """(Re)suggest the steps; earlier suggestions that weren't accepted go."""
    await llm.suggest_steps(
        session, plan_id, mode="initial", instructions=payload.instructions if payload else None
    )
    await publish_step_plan_changed(plan_id)
    return await _plan(session, plan_id)


@router.post("/{plan_id}/suggest-missing", response_model=StepPlanRead)
async def suggest_missing(
    plan_id: int,
    payload: StepGenerateRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> StepPlanRead:
    """Ask the model what the plan still misses; suggestions arrive as proposals."""
    await llm.suggest_steps(
        session, plan_id, mode="missing", instructions=payload.instructions if payload else None
    )
    await publish_step_plan_changed(plan_id)
    return await _plan(session, plan_id)


@router.post("/{plan_id}/start", response_model=StepPlanRead)
async def start_plan(
    plan_id: int,
    payload: StepStartRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> StepPlanRead:
    plan = await svc.start_plan(
        session, plan_id, accept_proposed=payload.accept_proposed if payload else True
    )
    if plan.automation_mode == AUTOMATION_AUTO:
        await runner.start(session, plan_id)
    await publish_step_plan_changed(plan_id)
    return await _plan(session, plan_id)


@router.post("/{plan_id}/complete", response_model=StepPlanRead)
async def complete_plan(plan_id: int, session: AsyncSession = Depends(get_session)) -> StepPlanRead:
    """Mark the plan done once its final output is built from approved steps."""
    await svc.complete_plan(session, plan_id)
    await publish_step_plan_changed(plan_id)
    return await _plan(session, plan_id)


@router.post("/{plan_id}/auto/start", response_model=StepPlanRead)
async def start_auto(plan_id: int, session: AsyncSession = Depends(get_session)) -> StepPlanRead:
    """Start (or resume) the automated run: it drafts and scores, never validates."""
    await runner.start(session, plan_id)
    await publish_step_plan_changed(plan_id)
    return await _plan(session, plan_id)


@router.post("/{plan_id}/auto/pause", response_model=StepPlanRead)
async def pause_auto(plan_id: int, session: AsyncSession = Depends(get_session)) -> StepPlanRead:
    """Stop the run once the step in flight is done."""
    await runner.pause(session, plan_id)
    await publish_step_plan_changed(plan_id)
    return await _plan(session, plan_id)


@router.post("/{plan_id}/assess", response_model=StepPlanRead)
async def assess_plan(plan_id: int, session: AsyncSession = Depends(get_session)) -> StepPlanRead:
    """Model self-assessment of the whole result against the original ask."""
    await llm.assess_plan(session, plan_id)
    await publish_step_plan_changed(plan_id)
    return await _plan(session, plan_id)


@router.post("/{plan_id}/steps", response_model=StepRead, status_code=status.HTTP_201_CREATED)
async def create_step(
    plan_id: int, payload: StepCreate, session: AsyncSession = Depends(get_session)
) -> StepRead:
    plan = await svc.get_plan(session, plan_id)
    step = await svc.create_step(session, plan, payload)
    await publish_step_plan_changed(plan_id)
    return await _step(session, step.id)


@router.post("/{plan_id}/review", response_model=StepPlanReview)
async def review_plan(plan_id: int, session: AsyncSession = Depends(get_session)) -> StepPlanReview:
    """A read-only critique of the plan: gaps, contradictions, weak steps."""
    return StepPlanReview(text=await llm.review_plan(session, plan_id))


@router.post("/{plan_id}/consolidation/chat", response_model=ChatRead)
async def consolidation_chat(
    plan_id: int, session: AsyncSession = Depends(get_session)
) -> ChatRead:
    chat = await svc.ensure_consolidation_chat(session, plan_id)
    await publish_step_plan_changed(plan_id)
    return await to_chat_read(session, chat)


@router.post("/{plan_id}/consolidate/generate", response_model=FinalDraftResult)
async def generate_final(
    plan_id: int,
    payload: ConsolidateRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> FinalDraftResult:
    draft = await llm.generate_final(
        session,
        plan_id,
        instructions=payload.instructions if payload else None,
        force=payload.force if payload else False,
        complete=True,
    )
    await publish_step_plan_changed(plan_id)
    return FinalDraftResult(plan=await _plan(session, plan_id), outcome=draft.outcome)


@router.post("/{plan_id}/final/from-message", response_model=StepPlanRead)
async def final_from_message(
    plan_id: int, payload: UseMessageRequest, session: AsyncSession = Depends(get_session)
) -> StepPlanRead:
    await llm.use_message_as_final(session, plan_id, payload.message_id, complete=True)
    await publish_step_plan_changed(plan_id)
    return await _plan(session, plan_id)


@router.put("/{plan_id}/final", response_model=StepPlanRead)
async def write_final(
    plan_id: int, payload: FinalWrite, session: AsyncSession = Depends(get_session)
) -> StepPlanRead:
    await llm.save_final(session, plan_id, payload.content)
    await publish_step_plan_changed(plan_id)
    return await _plan(session, plan_id)


@router.post("/{plan_id}/send-to-topic", response_model=MessageRead)
async def send_to_topic(
    plan_id: int,
    payload: SendToTopicRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> MessageRead:
    _, message = await llm.send_to_topic(
        session,
        plan_id,
        payload.text if payload else None,
        force=payload.force if payload else False,
    )
    await publish_step_plan_changed(plan_id)
    return message


# --- steps --------------------------------------------------------------------


@steps_router.patch("/{step_id}", response_model=StepRead)
async def update_step(
    step_id: int, payload: StepUpdate, session: AsyncSession = Depends(get_session)
) -> StepRead:
    step = await svc.update_step(session, step_id, payload)
    await publish_step_plan_changed(step.plan_id)
    return await _step(session, step_id)


@steps_router.delete("/{step_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_step(step_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    plan = await svc.delete_step(session, step_id)
    await publish_step_plan_changed(plan.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@steps_router.post("/{step_id}/move", response_model=StepRead)
async def move_step(
    step_id: int, payload: StepMove, session: AsyncSession = Depends(get_session)
) -> StepRead:
    step = await svc.move_step(session, step_id, payload.parent_step_id, payload.position)
    await publish_step_plan_changed(step.plan_id)
    return await _step(session, step_id)


@steps_router.post("/{step_id}/accept", response_model=StepRead)
async def accept_step(step_id: int, session: AsyncSession = Depends(get_session)) -> StepRead:
    step = await svc.accept_step(session, step_id)
    await publish_step_plan_changed(step.plan_id)
    return await _step(session, step_id)


@steps_router.post("/{step_id}/status", response_model=StepRead)
async def set_status(
    step_id: int, payload: StepStatusUpdate, session: AsyncSession = Depends(get_session)
) -> StepRead:
    step = await svc.set_step_status(session, step_id, payload.status)
    await publish_step_plan_changed(step.plan_id)
    return await _step(session, step_id)


@steps_router.put("/{step_id}/artifact", response_model=StepRead)
async def write_artifact(
    step_id: int, payload: StepArtifactWrite, session: AsyncSession = Depends(get_session)
) -> StepRead:
    step = await svc.write_artifact(session, step_id, payload.content, author=AUTHOR_USER)
    await publish_step_plan_changed(step.plan_id)
    return await _step(session, step_id)


@steps_router.get("/{step_id}/versions", response_model=list[StepArtifactVersionRead])
async def list_versions(
    step_id: int, session: AsyncSession = Depends(get_session)
) -> list[StepArtifactVersionRead]:
    return await svc.list_versions(session, step_id)


@steps_router.post("/{step_id}/artifact/revert", response_model=StepRead)
async def revert_artifact(
    step_id: int, payload: StepArtifactRevert, session: AsyncSession = Depends(get_session)
) -> StepRead:
    step = await svc.revert_artifact(session, step_id, payload.version)
    await publish_step_plan_changed(step.plan_id)
    return await _step(session, step_id)


@steps_router.post("/{step_id}/artifact/generate", response_model=ArtifactDraftResult)
async def generate_artifact(
    step_id: int,
    payload: StepGenerateRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> ArtifactDraftResult:
    draft = await llm.generate_artifact(
        session, step_id, instructions=payload.instructions if payload else None
    )
    await publish_step_plan_changed(draft.step.plan_id)
    return ArtifactDraftResult(step=await _step(session, step_id), outcome=draft.outcome)


@steps_router.post("/{step_id}/assess", response_model=StepRead)
async def assess_step(step_id: int, session: AsyncSession = Depends(get_session)) -> StepRead:
    """Model self-assessment of this step's artifact."""
    step = await llm.assess_step(session, step_id)
    await publish_step_plan_changed(step.plan_id)
    return await _step(session, step_id)


@steps_router.post("/{step_id}/artifact/from-message", response_model=StepRead)
async def artifact_from_message(
    step_id: int, payload: UseMessageRequest, session: AsyncSession = Depends(get_session)
) -> StepRead:
    step = await llm.use_message_as_artifact(session, step_id, payload.message_id)
    await publish_step_plan_changed(step.plan_id)
    return await _step(session, step_id)


@steps_router.post("/{step_id}/suggest-substeps", response_model=StepPlanRead)
async def suggest_substeps(
    step_id: int,
    payload: StepGenerateRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> StepPlanRead:
    step = await svc.get_step(session, step_id)
    await llm.suggest_steps(
        session,
        step.plan_id,
        mode="substeps",
        parent_step_id=step_id,
        instructions=payload.instructions if payload else None,
    )
    await publish_step_plan_changed(step.plan_id)
    return await _plan(session, step.plan_id)


@steps_router.post("/{step_id}/chat", response_model=ChatRead)
async def step_chat(step_id: int, session: AsyncSession = Depends(get_session)) -> ChatRead:
    chat = await svc.ensure_step_chat(session, step_id)
    step = await svc.get_step(session, step_id)
    await publish_step_plan_changed(step.plan_id)
    return await to_chat_read(session, chat)
