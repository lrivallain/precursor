"""Automated run of a step-by-step plan.

The run drafts the steps that still have nothing, scores each draft, then drafts
a provisional final output and scores the whole. It never validates anything:
approval stays with the user. It also never touches a validated step, a step
that already has an artifact, or an existing final output, so resuming a run
(or running it on a plan the user has been working in) can't overwrite work.

One asyncio task per plan, in this process only. ``auto_state`` is persisted so
the UI shows it; a run that died with the process reads as paused (see
``step_plans.RUNNING``).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models.step_plan import (
    AUTO_IDLE,
    AUTO_PAUSED,
    AUTO_RUNNING,
    AUTOMATION_AUTO,
    PLAN_ACTIVE,
    PLAN_CONSOLIDATING,
    STEP_VALIDATED,
    StepPlan,
)
from precursor.backend.services import step_plan_llm as llm
from precursor.backend.services import step_plans as svc
from precursor.backend.services.events import publish_step_plan_changed, set_current_client_id

log = logging.getLogger(__name__)

_TASKS: dict[int, asyncio.Task[None]] = {}


def _live(plan: StepPlan) -> bool:
    return plan.auto_state == AUTO_RUNNING and plan.status in (PLAN_ACTIVE, PLAN_CONSOLIDATING)


async def _set_state(plan_id: int, state: str) -> None:
    async with SessionLocal() as session:
        plan = await svc.get_plan(session, plan_id)
        plan.auto_state = state
        await session.commit()
    await publish_step_plan_changed(plan_id)


async def _note(plan_id: int, text: str) -> None:
    async with SessionLocal() as session:
        chat = await svc.ensure_consolidation_chat(session, plan_id)
        await llm.trace_in_chat(session, chat.id, text)


async def _step_title(step_id: int) -> str:
    async with SessionLocal() as session:
        return (await svc.get_step(session, step_id)).title


def _outcome_note(title: str, draft: llm.ArtifactDraft) -> str:
    if draft.outcome == "published":
        step = draft.step
        score = svc.parse_assessment(step.assessment_json)
        scored = f", scored {score.score}/100" if score else ""
        return (
            f"Drafted «{title}» (version {step.artifact_version}{scored}). Waiting for your review."
        )
    if draft.outcome == "discussion":
        return f"«{title}»: the model answered in the discussion without a deliverable."
    return f"«{title}»: waiting for a tool approval."


async def _next_step(plan_id: int, skip: set[int]) -> int | None:
    """The first working step that still has nothing and hasn't been approved."""
    async with SessionLocal() as session:
        plan = await svc.get_plan(session, plan_id)
        if not _live(plan):
            return None
        for step in svc.working_leaves(await svc.plan_steps(session, plan_id)):
            if step.id in skip or step.artifact.strip() or step.status == STEP_VALIDATED:
                continue
            return step.id
    return None


async def _still_running(plan_id: int) -> bool:
    async with SessionLocal() as session:
        return _live(await svc.get_plan(session, plan_id))


async def _run(plan_id: int) -> None:
    done: set[int] = set()
    # The task outlives the request that started it: its events are for every
    # window, the one that pressed Start included.
    set_current_client_id(None)
    try:
        await _note(
            plan_id,
            "Automated run started: the model drafts and scores the steps. "
            "It validates nothing: every draft waits for your review.",
        )
        while (step_id := await _next_step(plan_id, done)) is not None:
            done.add(step_id)
            title = await _step_title(step_id)
            svc.CURRENT_STEP[plan_id] = step_id
            await _note(plan_id, f"Drafting «{title}»…")
            await publish_step_plan_changed(plan_id)
            async with SessionLocal() as session:
                draft = await llm.generate_artifact(session, step_id)
            svc.CURRENT_STEP.pop(plan_id, None)
            await _note(plan_id, _outcome_note(title, draft))
            await publish_step_plan_changed(plan_id)
            if draft.outcome == "awaiting_approval":
                await _note(plan_id, "Paused: a tool call in a step needs your approval.")
                await _set_state(plan_id, AUTO_PAUSED)
                return
        if not await _still_running(plan_id):
            return
        await _finish(plan_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        log.exception("Automated run of step plan %s failed", plan_id)
        await _note(plan_id, f"Automated run stopped: {detail}")
        await _set_state(plan_id, AUTO_PAUSED)
    finally:
        svc.CURRENT_STEP.pop(plan_id, None)
        svc.RUNNING.discard(plan_id)
        _TASKS.pop(plan_id, None)
        await publish_step_plan_changed(plan_id)


async def _finish(plan_id: int) -> None:
    """Every empty step is drafted: synthesise a provisional final and score it."""
    async with SessionLocal() as session:
        plan = await svc.get_plan(session, plan_id)
        steps = await svc.plan_steps(session, plan_id)
        has_work = any(s.artifact.strip() for s in svc.working_leaves(steps))
        if has_work and not (plan.final_output or "").strip():
            outcome = (await llm.generate_final(session, plan_id, force=True)).outcome
            await publish_step_plan_changed(plan_id)
            if outcome == "awaiting_approval":
                await _note(
                    plan_id, "Paused: a tool call in the consolidation needs your approval."
                )
                await _set_state(plan_id, AUTO_PAUSED)
                return
        elif has_work:
            await llm.assess_plan(session, plan_id)
    await _note(
        plan_id,
        "Automated run finished. Drafts and the provisional output are waiting for "
        "your review; nothing is validated.",
    )
    await _set_state(plan_id, AUTO_IDLE)


async def start(session: AsyncSession, plan_id: int) -> StepPlan:
    plan = await svc.get_plan(session, plan_id)
    if plan.automation_mode != AUTOMATION_AUTO:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Switch the plan to fully automated to run it."
        )
    if plan.status not in (PLAN_ACTIVE, PLAN_CONSOLIDATING):
        raise HTTPException(status.HTTP_409_CONFLICT, "Start the plan first.")
    if plan_id in svc.RUNNING:
        # Paused a moment ago and the task hasn't wound down yet: let it carry on.
        plan.auto_state = AUTO_RUNNING
        await session.commit()
        return plan
    plan.auto_state = AUTO_RUNNING
    await session.commit()
    svc.RUNNING.add(plan_id)
    _TASKS[plan_id] = asyncio.create_task(_run(plan_id))
    return plan


async def pause(session: AsyncSession, plan_id: int) -> StepPlan:
    """Stop after the step in flight; the run resumes with ``start``."""
    plan = await svc.get_plan(session, plan_id)
    if plan.auto_state == AUTO_RUNNING:
        plan.auto_state = AUTO_PAUSED
        await session.commit()
    return plan


async def join(plan_id: int) -> None:
    """Wait for a plan's run to end (tests, shutdown)."""
    task = _TASKS.get(plan_id)
    if task is not None:
        await asyncio.shield(task)
