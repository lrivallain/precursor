"""Automated run of a step-by-step plan.

The run drafts the steps that still have nothing, scores each draft, then drafts
a provisional final output and scores the whole. Under balanced, autonomous and
plan delegation policies, each checked step is repaired and validated before the
next one starts; unresolved weak work pauses for the user. Other policies leave
approval with the user. The run never touches a validated step, a step
that already has an artifact, or an existing final output, so resuming a run
(or running it on a plan the user has been working in) can't overwrite work.

One asyncio task per plan, in this process only. ``auto_state`` is persisted so
the UI shows it; a run that died with the process reads as paused (see
``step_plans.RUNNING``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

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
    STEP_TO_VALIDATE,
    STEP_VALIDATED,
    StepPlan,
)
from precursor.backend.services import step_plan_llm as llm
from precursor.backend.services import step_plans as svc
from precursor.backend.services.events import publish_step_plan_changed, set_current_client_id

log = logging.getLogger(__name__)

_TASKS: dict[int, asyncio.Task[None]] = {}

_ATTEMPTS = 3
_RETRY_DELAY = 5.0


async def _retried(
    plan_id: int, call: Any, session: AsyncSession, *args: Any, **kwargs: Any
) -> Any:
    """Run a model call, retrying transient failures (a timeout, a 5xx) so one
    flaky request doesn't stop an unattended run."""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            return await call(session, *args, **kwargs)
        except asyncio.CancelledError:
            raise
        except HTTPException as exc:
            if exc.status_code < 500 or attempt == _ATTEMPTS:
                raise
            detail = exc.detail
        except Exception as exc:
            if attempt == _ATTEMPTS:
                raise
            detail = str(exc) or type(exc).__name__
        await session.rollback()
        await _note(
            plan_id, f"The model call failed ({detail}). Retrying ({attempt}/{_ATTEMPTS - 1})…"
        )
        await asyncio.sleep(_RETRY_DELAY * attempt)


def _live(plan: StepPlan) -> bool:
    return plan.auto_state == AUTO_RUNNING and plan.status in (PLAN_ACTIVE, PLAN_CONSOLIDATING)


_NO_QUESTIONS = (
    "Do not ask questions or offer options: write the deliverable now, marked as a "
    "deliverable, from the plan's ask and what is already known. Use your tools and "
    "skills if the step calls for them; do not imitate their output by hand."
)


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


def _outcome_note(title: str, draft: llm.ArtifactDraft, *, automatic_review: bool = False) -> str:
    if draft.outcome == "published":
        step = draft.step
        score = svc.parse_assessment(step.assessment_json)
        scored = f", scored {score.score}/100" if score else ""
        next_action = (
            " Ready for the automatic check." if automatic_review else " Waiting for review."
        )
        return f"Drafted «{title}» (version {step.artifact_version}{scored}).{next_action}"
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
        async with SessionLocal() as session:
            policy = (await svc.get_plan(session, plan_id)).delegation_policy
        tail = {
            "autonomous": "Checked steps are validated automatically.",
            "balanced": "Checked steps are validated automatically; you review the final output.",
        }.get(policy or "", "It validates nothing: every draft waits for your review.")
        await _note(
            plan_id, f"Automated run started: the model drafts and scores the steps. {tail}"
        )
        while True:
            step_id = await _next_step(plan_id, done)
            if step_id is None:
                break
            unresolved = await _unresolved_step(plan_id, before_step=step_id)
            if unresolved is not None:
                await _note(
                    plan_id,
                    f"«{unresolved[0]}» {unresolved[1]}. Pausing before later steps rely on it; "
                    "review this step, then resume.",
                )
                await _set_state(plan_id, AUTO_PAUSED)
                return
            done.add(step_id)
            title = await _step_title(step_id)
            svc.CURRENT_STEP[plan_id] = step_id
            await _note(plan_id, f"Drafting «{title}»…")
            await publish_step_plan_changed(plan_id)
            async with SessionLocal() as session:
                draft = await _retried(plan_id, llm.generate_artifact, session, step_id)
            if draft.outcome == "discussion":
                await _note(
                    plan_id,
                    _outcome_note(
                        title, draft, automatic_review=policy in ("balanced", "autonomous", "plan")
                    )
                    + " Asking once more.",
                )
                async with SessionLocal() as session:
                    draft = await _retried(
                        plan_id, llm.generate_artifact, session, step_id, instructions=_NO_QUESTIONS
                    )
            svc.CURRENT_STEP.pop(plan_id, None)
            await _note(
                plan_id,
                _outcome_note(
                    title, draft, automatic_review=policy in ("balanced", "autonomous", "plan")
                ),
            )
            await publish_step_plan_changed(plan_id)
            if draft.outcome == "awaiting_approval":
                await _note(plan_id, "Paused: a tool call in a step needs your approval.")
                await _set_state(plan_id, AUTO_PAUSED)
                return
            if draft.outcome == "discussion":
                await _note(
                    plan_id,
                    f"«{title}» has no artifact after the follow-up. Pausing before later steps "
                    "rely on it; finish this step, then resume.",
                )
                await _set_state(plan_id, AUTO_PAUSED)
                return
            if not await _review_step_before_dependents(plan_id, step_id):
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
        empty = [
            s.title
            for s in svc.working_leaves(steps)
            if not s.artifact.strip() and s.status != STEP_VALIDATED
        ]
        if empty:
            await _note(
                plan_id,
                "Paused: no draft for " + ", ".join(f"«{t}»" for t in empty) + ". Answer in "
                "the step's discussion or write it, then resume.",
            )
            await _set_state(plan_id, AUTO_PAUSED)
            return
        has_work = any(s.artifact.strip() for s in svc.working_leaves(steps))
        if has_work and not (plan.final_output or "").strip():
            outcome = (
                await _retried(plan_id, llm.generate_final, session, plan_id, force=True)
            ).outcome
            await publish_step_plan_changed(plan_id)
            if outcome == "awaiting_approval":
                await _note(
                    plan_id, "Paused: a tool call in the consolidation needs your approval."
                )
                await _set_state(plan_id, AUTO_PAUSED)
                return
        elif has_work:
            await _retried(plan_id, llm.assess_plan, session, plan_id)
    async with SessionLocal() as session:
        policy = (await svc.get_plan(session, plan_id)).delegation_policy
    if policy == "plan":
        policy = "balanced"
    if policy in ("balanced", "autonomous") and await _review(plan_id, policy):
        await _set_state(plan_id, AUTO_IDLE)
        return
    await _note(
        plan_id,
        "Automated run finished. Drafts and the provisional output are waiting for your review.",
    )
    await _set_state(plan_id, AUTO_IDLE)


_HAND_BACKS: set[asyncio.Task[None]] = set()


def hand_back(topic_id: int, title: str, *, reviewed_by_user: bool) -> None:
    """Wake the topic's model once the plan's output is filed there, without blocking."""
    from precursor.backend.services.turn import run_topic_turn

    if reviewed_by_user:
        prompt = (
            f"The user finished reviewing the step-by-step plan «{title}» you delegated and "
            "its final output was filed above. Carry on by yourself: check it against the "
            "original ask, act on it, and say what you do next. Do not wait for the user."
        )
    else:
        prompt = (
            f"The step-by-step plan «{title}» you delegated is finished and its final "
            "output was filed above. Review it against the user's original ask: say "
            "what was delivered, flag anything weak or missing, and propose next steps."
        )

    async def _go() -> None:
        try:
            await run_topic_turn(topic_id, prompt)
        except Exception:
            log.exception("Hand-back to topic %s failed", topic_id)

    task = asyncio.create_task(_go())
    _HAND_BACKS.add(task)
    task.add_done_callback(_HAND_BACKS.discard)


_FIX_FILE = (
    "The app checked the file you gave and found: {problems}. Fix it (rebuild the file with "
    "your tools if needed), then give its absolute path again as the artifact."
)
_FIX_WEAK = (
    "The step was scored {score}/100. The reviewer's gaps: {gaps}. Close them using your tools "
    "(read the file back if needed) and give the improved artifact."
)
_WEAK_BELOW = 60
_HOLD_BELOW = 50
_FIX_FINAL_PATH = (
    " The app checked the paths in your previous final output and found: {problems}. "
    "The files the steps really built are: {built}. Give exactly those paths, with a short summary and how to open them; do not paste or rewrite the file's content."
)


async def _unresolved_step(plan_id: int, *, before_step: int) -> tuple[str, str] | None:
    """Find a preceding weak draft that must not be used by the next step."""
    async with SessionLocal() as session:
        plan = await svc.get_plan(session, plan_id)
        if plan.delegation_policy not in ("balanced", "autonomous", "plan"):
            return None
        for step in svc.working_leaves(await svc.plan_steps(session, plan_id)):
            if step.id == before_step:
                break
            if step.status != STEP_TO_VALIDATE or not step.artifact.strip():
                continue
            problems = llm.step_check_failures(step)
            weak = llm.step_weakness(step)
            if problems:
                return step.title, "failed a deliverable check: " + "; ".join(problems[:3])
            if weak and weak[0] < _HOLD_BELOW:
                return step.title, f"still scores {weak[0]}/100"
    return None


async def _review_step_before_dependents(plan_id: int, step_id: int) -> bool:
    """Repair and validate an automatic draft before later steps can rely on it."""
    async with SessionLocal() as session:
        plan = await svc.get_plan(session, plan_id)
        if plan.delegation_policy not in ("balanced", "autonomous", "plan"):
            return True
        step = await svc.get_step(session, step_id)
        if not step.artifact.strip() or step.status != STEP_TO_VALIDATE:
            return False
        title = step.title
        problems = llm.step_check_failures(step)
        weak = llm.step_weakness(step)
        if problems or (weak and weak[0] < _WEAK_BELOW):
            reasons: list[str] = []
            instructions: list[str] = []
            if problems:
                reasons.append("failed a check")
                instructions.append(_FIX_FILE.format(problems="; ".join(problems)))
            if weak and weak[0] < _WEAK_BELOW:
                reasons.append(f"scored {weak[0]}/100")
                instructions.append(
                    _FIX_WEAK.format(score=weak[0], gaps="; ".join(weak[1][:5]) or "none listed")
                )
            await _note(
                plan_id,
                f"«{title}» {' and '.join(reasons)}. Redrafting once before later steps rely on it.",
            )
            draft = await _retried(
                plan_id,
                llm.generate_artifact,
                session,
                step_id,
                instructions="\n\n".join(instructions),
            )
            if draft.outcome != "published" or not draft.step.artifact.strip():
                await _note(
                    plan_id,
                    f"«{title}» has no improved artifact. Pausing before drafting later steps; "
                    "review this step, then resume.",
                )
                return False
            step = await svc.get_step(session, step_id)
            problems = llm.step_check_failures(step)
            weak = llm.step_weakness(step)
            if problems or (weak and weak[0] < _HOLD_BELOW):
                if problems:
                    reason = "failed a check: " + "; ".join(problems[:3])
                else:
                    reason = f"still scores {weak[0] if weak else 0}/100"
                await _note(
                    plan_id,
                    f"«{title}» is still weak ({reason}). Pausing before later steps rely on it; "
                    "review this step, then resume.",
                )
                return False
        await svc.set_step_status(session, step_id, STEP_VALIDATED)
        await _note(plan_id, f"«{title}» is validated before the next step starts.")
    await publish_step_plan_changed(plan_id)
    return True


async def _review(plan_id: int, policy: str) -> bool:
    """Close a delegated plan as far as its topic's autonomy allows.

    Both policies validate the drafts and rebuild the final output (no longer
    provisional). ``autonomous`` also files it in the topic and wakes the topic's
    model; ``balanced`` leaves that last review to the user. ``False`` leaves the
    plan for a person to review.
    """
    try:
        async with SessionLocal() as session:
            plan = await svc.get_plan(session, plan_id)
            topic_id = plan.source_topic_id
            if topic_id is None:
                return False
            steps = await svc.plan_steps(session, plan_id)
            for step in svc.working_leaves(steps):
                if step.status != STEP_TO_VALIDATE or not step.artifact.strip():
                    continue
                problems = llm.step_check_failures(step)
                if problems:
                    await _note(
                        plan_id,
                        f"«{step.title}» failed a check ({'; '.join(problems)}). Redrafting once.",
                    )
                    step = (
                        await _retried(
                            plan_id,
                            llm.generate_artifact,
                            session,
                            step.id,
                            instructions=_FIX_FILE.format(problems="; ".join(problems)),
                        )
                    ).step
                    problems = llm.step_check_failures(step)
                    if step.status != STEP_TO_VALIDATE or problems:
                        await _note(
                            plan_id,
                            f"«{step.title}» still fails a check"
                            + (f" ({'; '.join(problems)})" if problems else "")
                            + ". Please review the step yourself.",
                        )
                        return False
                weak = llm.step_weakness(step)
                if weak and weak[0] < _WEAK_BELOW:
                    await _note(
                        plan_id,
                        f"«{step.title}» scored {weak[0]}/100. Redrafting once to close its gaps.",
                    )
                    step = (
                        await _retried(
                            plan_id,
                            llm.generate_artifact,
                            session,
                            step.id,
                            instructions=_FIX_WEAK.format(
                                score=weak[0], gaps="; ".join(weak[1][:5]) or "none listed"
                            ),
                        )
                    ).step
                    weak = llm.step_weakness(step)
                    if step.status != STEP_TO_VALIDATE or (weak and weak[0] < _HOLD_BELOW):
                        await _note(
                            plan_id,
                            f"«{step.title}» is still weak"
                            + (f" ({weak[0]}/100)" if weak else "")
                            + ". Please review the step yourself.",
                        )
                        return False
                await svc.set_step_status(session, step.id, STEP_VALIDATED)
            final = await _retried(plan_id, llm.generate_final, session, plan_id, force=True)
            if final.outcome == "discussion":
                final = await _retried(
                    plan_id,
                    llm.generate_final,
                    session,
                    plan_id,
                    force=True,
                    instructions=_NO_QUESTIONS,
                )
            if final.outcome == "published":
                problems, built = await llm.final_file_problems(session, plan_id)
                if problems:
                    await _note(
                        plan_id,
                        f"The final output has a file problem ({'; '.join(problems)}). "
                        "Rebuilding it once.",
                    )
                    final = await _retried(
                        plan_id,
                        llm.generate_final,
                        session,
                        plan_id,
                        force=True,
                        instructions=_NO_QUESTIONS
                        + _FIX_FINAL_PATH.format(
                            problems="; ".join(problems), built=", ".join(built)
                        ),
                    )
                    problems, _ = await llm.final_file_problems(session, plan_id)
                    if problems:
                        await _note(
                            plan_id,
                            "The final output still has a file problem. It was "
                            "not filed in the topic: review it, then send it yourself.",
                        )
                        return False
            if final.outcome != "published":
                await _note(
                    plan_id,
                    "The model did not write the final output. Regenerate it from the plan, "
                    "then send it to the topic.",
                )
                return False
            plan = await svc.get_plan(session, plan_id)
            await session.refresh(plan)
            verdict = svc.parse_assessment(plan.assessment_json)
            if verdict is not None and verdict.score < _HOLD_BELOW:
                await _note(
                    plan_id,
                    f"The final output scored {verdict.score}/100 against the ask "
                    f"({'; '.join(verdict.gaps[:3]) or 'no gaps listed'}). It was not filed "
                    "in the topic: review it, then send it yourself.",
                )
                return False
            if policy == "autonomous":
                plan, _ = await llm.send_to_topic(session, plan_id, None)
            title = plan.title
        await publish_step_plan_changed(plan_id)
        if policy == "autonomous":
            await _note(
                plan_id,
                "Drafts validated and the final output filed in the topic, which now reviews it.",
            )
            hand_back(topic_id, title, reviewed_by_user=False)
        else:
            await _note(
                plan_id,
                "Drafts validated. The final output is ready: review it, then send it to "
                "the topic.",
            )
    except Exception as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        log.exception("Review of step plan %s failed", plan_id)
        await _note(plan_id, f"Automatic review stopped, please review the plan yourself: {detail}")
        return False
    return True


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
