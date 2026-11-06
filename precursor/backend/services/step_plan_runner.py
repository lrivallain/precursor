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
import json
import logging
from typing import Any, Literal

from fastapi import HTTPException, status
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import Message, MessageRole
from precursor.backend.models.step_plan import (
    AUTO_IDLE,
    AUTO_PAUSED,
    AUTO_RUNNING,
    AUTOMATION_AUTO,
    PLAN_ACTIVE,
    PLAN_CONSOLIDATING,
    PLAN_DRAFTING,
    STEP_TO_VALIDATE,
    STEP_VALIDATED,
    Step,
    StepPlan,
    StepPlanFinalVersion,
)
from precursor.backend.schemas.step_plan import StepPlanUpdate
from precursor.backend.services import approvals as approval_service
from precursor.backend.services import step_plan_llm as llm
from precursor.backend.services import step_plans as svc
from precursor.backend.services.events import (
    get_bus,
    is_streaming,
    publish_step_plan_changed,
    set_current_client_id,
)

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


async def _set_idle_if_running(plan_id: int) -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(StepPlan)
            .where(StepPlan.id == plan_id, StepPlan.auto_state == AUTO_RUNNING)
            .values(auto_state=AUTO_IDLE)
        )
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
            plan_id,
            f"Automated run started: the model drafts and scores steps one at a time. {tail}",
        )
        while True:
            step_id = await _next_step(plan_id, done)
            if step_id is None:
                break
            if not await _assess_existing_steps(plan_id, before_step=step_id):
                await _set_state(plan_id, AUTO_PAUSED)
                return
            if not await _still_running(plan_id):
                return
            unresolved = await _unresolved_step(plan_id, before_step=step_id)
            if unresolved is not None:
                await _note(
                    plan_id,
                    f"«{unresolved[0]}» {unresolved[1]}. Pausing before later steps rely on it; "
                    "review this step, then resume.",
                )
                await _set_state(plan_id, AUTO_PAUSED)
                return
            if not await _still_running(plan_id):
                return
            title = await _step_title(step_id)
            async with svc.plan_lock(plan_id):
                if not await _still_running(plan_id):
                    return
                svc.CURRENT_STEP[plan_id] = step_id
            done.add(step_id)
            await _note(plan_id, f"Drafting «{title}»…")
            await publish_step_plan_changed(plan_id)
            async with SessionLocal() as session:
                draft = await _retried(
                    plan_id, llm.generate_artifact, session, step_id, automatic=True
                )
            if draft.outcome == "discussion" and await _still_running(plan_id):
                await _note(
                    plan_id,
                    _outcome_note(
                        title, draft, automatic_review=policy in ("balanced", "autonomous", "plan")
                    )
                    + " Asking once more.",
                )
                async with SessionLocal() as session:
                    draft = await _retried(
                        plan_id,
                        llm.generate_artifact,
                        session,
                        step_id,
                        instructions=_NO_QUESTIONS,
                        automatic=True,
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
            if not await _still_running(plan_id):
                return
            if not await _review_step_before_dependents(plan_id, step_id):
                await _set_state(plan_id, AUTO_PAUSED)
                return
        if not await _still_running(plan_id):
            return
        if not await _assess_existing_steps(plan_id):
            await _set_state(plan_id, AUTO_PAUSED)
            return
        if not await _still_running(plan_id):
            return
        unresolved = await _unresolved_step(plan_id)
        if unresolved is not None:
            await _note(
                plan_id,
                f"«{unresolved[0]}» {unresolved[1]}. Review this step before the final output.",
            )
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
    if not await _still_running(plan_id):
        return
    generated_final = False
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
        amendments = svc.plan_amendments(plan)
        amendment_needs_final = bool(
            plan.final_stale
            and amendments
            and amendments[-1].base_final_version == plan.final_version
        )
        if has_work and (not (plan.final_output or "").strip() or amendment_needs_final):
            final = await _retried(
                plan_id, llm.generate_final, session, plan_id, force=True, automatic=True
            )
            if final.outcome == "discussion" and await _still_running(plan_id):
                final = await _retried(
                    plan_id,
                    llm.generate_final,
                    session,
                    plan_id,
                    force=True,
                    instructions=_NO_QUESTIONS,
                    automatic=True,
                )
            outcome = final.outcome
            generated_final = outcome == "published"
            await publish_step_plan_changed(plan_id)
            if outcome == "awaiting_approval":
                await _note(
                    plan_id, "Paused: a tool call in the consolidation needs your approval."
                )
                await _set_state(plan_id, AUTO_PAUSED)
                return
        elif has_work and plan.final_stale:
            await _note(
                plan_id,
                "The existing final output is out of date and was preserved. "
                "Review or regenerate it before continuing.",
            )
            await _set_state(plan_id, AUTO_PAUSED)
            return
        elif has_work:
            await _retried(plan_id, llm.assess_plan, session, plan_id)
    if not await _still_running(plan_id):
        return
    async with SessionLocal() as session:
        policy = (await svc.get_plan(session, plan_id)).delegation_policy
    if policy == "plan":
        policy = "balanced"
    if policy in ("balanced", "autonomous") and await _review(
        plan_id, policy, generated_final=generated_final, require_running=True
    ):
        await _set_idle_if_running(plan_id)
        return
    if not await _still_running(plan_id):
        return
    await _note(
        plan_id,
        "Automated run finished without filing. Review the current drafts and final output "
        "before continuing.",
    )
    await _set_idle_if_running(plan_id)


_HAND_BACKS: set[asyncio.Task[None]] = set()


async def _source_reviewed_final(
    source_kind: Literal["topic", "chat"],
    source_id: int,
    plan_id: int,
    posted_message_id: int,
    final_version: int,
) -> bool:
    column = Message.topic_id if source_kind == "topic" else Message.chat_id
    async with SessionLocal() as session:
        posted = await session.get(Message, posted_message_id)
        if posted is None or getattr(posted, f"{source_kind}_id") != source_id:
            return False
        version = await session.scalar(
            select(StepPlanFinalVersion).where(
                StepPlanFinalVersion.plan_id == plan_id,
                StepPlanFinalVersion.version == final_version,
            )
        )
        prior_reply = await session.scalar(
            select(Message.id)
            .where(
                column == source_id,
                Message.id < posted_message_id,
                Message.role == MessageRole.ASSISTANT,
                or_(Message.tool_calls.is_(None), Message.tool_calls == ""),
            )
            .order_by(Message.id.desc())
            .limit(1)
        )
        rows = list(
            (
                await session.execute(
                    select(Message)
                    .where(column == source_id, Message.id > (prior_reply or 0))
                    .order_by(Message.id)
                )
            ).scalars()
        )
        expected_text = version.content if version is not None else None
        posted_text = posted.content
    saw_final = False
    for row in rows:
        if row.role == MessageRole.TOOL:
            try:
                metadata = json.loads(row.tool_calls or "{}")
                result = json.loads(row.content)
            except (TypeError, ValueError):
                continue
            if not isinstance(metadata, dict) or not isinstance(result, dict):
                continue
            if metadata.get("is_error"):
                continue
            name = str(metadata.get("name", ""))
            final = result.get("final_output")
            if (
                name.endswith("__get_step_plan")
                and result.get("plan_id") == plan_id
                and isinstance(final, dict)
                and final.get("version") == final_version
                and not final.get("stale")
                and not final.get("provisional")
                and bool(expected_text and expected_text.strip())
                and final.get("text") == expected_text
            ):
                saw_final = True
            messages = result.get("messages")
            source_tool = "__list_messages" if source_kind == "topic" else "__list_chat_messages"
            if (
                name.endswith(source_tool)
                and result.get(f"{source_kind}_id") == source_id
                and isinstance(messages, list)
                and any(
                    isinstance(item, dict)
                    and item.get("id") == posted_message_id
                    and bool(posted_text.strip())
                    and item.get("content") == posted_text
                    for item in messages
                )
            ):
                saw_final = True
        elif row.role == MessageRole.ASSISTANT and not row.tool_calls:
            if row.id > posted_message_id and saw_final and row.content.strip():
                return True
            saw_final = False
    return False


async def _source_busy(source_kind: Literal["topic", "chat"], source_id: int) -> bool:
    if is_streaming(source_kind, source_id):
        return True
    async with SessionLocal() as session:
        approvals = await approval_service.list_approvals(session, source_kind, source_id)
    return bool(approvals) or is_streaming(source_kind, source_id)


async def _wait_for_source_idle(source_kind: Literal["topic", "chat"], source_id: int) -> None:
    async with get_bus().subscribe() as events:
        while await _source_busy(source_kind, source_id):
            try:
                await asyncio.wait_for(events.get(), timeout=2)
            except TimeoutError:
                continue


async def _hand_back_context(
    source_kind: Literal["topic", "chat"],
    source_id: int,
    plan_id: int | None,
    posted_message_id: int | None,
    final_version: int | None,
) -> tuple[bool, int | None]:
    if plan_id is None or posted_message_id is None or final_version is None:
        return True, None
    async with SessionLocal() as session:
        plan = await session.get(StepPlan, plan_id)
        if (
            plan is None
            or getattr(plan, f"source_{source_kind}_id") != source_id
            or plan.final_version != final_version
            or plan.final_stale
            or plan.final_provisional
            or not await svc.final_filed_in_current_source(session, plan)
        ):
            return False, None
        version = await session.scalar(
            select(StepPlanFinalVersion).where(
                StepPlanFinalVersion.plan_id == plan_id,
                StepPlanFinalVersion.version == final_version,
            )
        )
        if version is None or version.posted_message_id != posted_message_id:
            return False, None
        assessment = svc.parse_assessment(plan.assessment_json)
        steps = await svc.plan_steps(session, plan_id)
        if (
            assessment is None
            or assessment.scope != "final"
            or plan.assessed_sig != svc.plan_signature(plan, steps)
        ):
            return True, None
        return True, assessment.score


def hand_back(
    source_id: int,
    title: str,
    *,
    reviewed_by_user: bool,
    source_kind: Literal["topic", "chat"] = "topic",
    plan_id: int | None = None,
    posted_message_id: int | None = None,
    final_version: int | None = None,
) -> None:
    """Wake the source conversation once the plan's output is filed there."""
    from precursor.backend.services.turn import run_chat_turn, run_topic_turn

    if reviewed_by_user:
        prompt = (
            f"The user finished reviewing the step-by-step plan «{title}» you delegated and "
            "its final output was filed above. Carry on by yourself: check it against the "
            "original ask, act within its constraints, and say what you do next. Do not "
            "wait for the user."
        )
    else:
        prompt = (
            f"The step-by-step plan «{title}» you delegated is finished and its final "
            "output was filed above. Review it against the user's original ask: say "
            "what was delivered, flag anything weak or missing, and propose next steps."
        )
    prompt += (
        " Keep your response concise and do not post a second copy of the filed output "
        "unless the user asks for one. Do not state that no file was created or no lookup "
        "occurred, or that those process restrictions were satisfied: the available tool "
        "record cannot prove those actions were absent. If the request has process restrictions, "
        "say that compliance is unverified unless recorded actions establish a violation. "
        "Check the actual result against the request; do not claim it is verified, complete or "
        "compliant solely because of a model assessment or score. Do not invent a score or "
        "confuse a step's score with the final-output score. Only report an exact final-output "
        "score supplied in this hand-back, as a model estimate rather than proof."
    )

    async def _go() -> None:
        try:
            while True:
                await _wait_for_source_idle(source_kind, source_id)
                already_reviewed = (
                    plan_id is not None
                    and posted_message_id is not None
                    and final_version is not None
                    and await _source_reviewed_final(
                        source_kind, source_id, plan_id, posted_message_id, final_version
                    )
                )
                current, score = await _hand_back_context(
                    source_kind, source_id, plan_id, posted_message_id, final_version
                )
                if await _source_busy(source_kind, source_id):
                    continue
                if already_reviewed:
                    return
                if not current:
                    log.info("Skipping superseded hand-back for plan %s", plan_id)
                    return
                break
            score_context = (
                f" The current final-output model assessment is {score}/100."
                if score is not None
                else " No current final-output score is available; do not report one."
            )
            if source_kind == "topic":
                await run_topic_turn(source_id, prompt + score_context)
            else:
                await run_chat_turn(source_id, prompt + score_context)
        except Exception:
            log.exception("Hand-back to %s %s failed", source_kind, source_id)

    task = asyncio.create_task(_go())
    _HAND_BACKS.add(task)
    task.add_done_callback(_HAND_BACKS.discard)


_FIX_FILE = (
    "The app checked the file you gave and found: {problems}. Fix it (rebuild the file with "
    "your tools if needed), then give its absolute path again as the artifact."
)
_REVIEW_FEEDBACK = (
    "Review feedback can be wrong and is not a new user requirement. Verify each finding "
    "against the original request, accepted follow-ups, the current step instruction when "
    "applicable, and the current result. Preserve the user's constraints and do not invent "
    "facts, requirements, or actions to satisfy a mistaken finding. "
)
_FIX_WEAK = (
    "The step was scored {score}/100. The reviewer reported potential gaps or contradictions: "
    "{gaps}. "
    f"{_REVIEW_FEEDBACK}"
    "Correct genuine issues in the artifact itself, not just the explanation. Use your tools "
    "only when appropriate and allowed for the requested work, and check its actual structure "
    "and counts against each stated constraint before giving the improved artifact. It must "
    "remain this step's own result, not a section that belongs to another step."
)
_FIX_FINAL_GAPS = (
    " The final assessment reported potential gaps or contradictions: {gaps}. "
    f"{_REVIEW_FEEDBACK}"
    "Correct genuine issues in the final deliverable itself while preserving approved work. "
    "Do not invent evidence or take unrelated actions to satisfy the review."
)
_WEAK_BELOW = 60
_HOLD_BELOW = 50
_FIX_FINAL_PATH = (
    " The app checked the paths in your previous final output and found: {problems}. "
    "The files the steps really built are: {built}. Give exactly those paths, with a short summary and how to open them; do not paste or rewrite the file's content."
)


async def _unresolved_step(
    plan_id: int, *, before_step: int | None = None, ignore_unassessed: bool = False
) -> tuple[str, str] | None:
    """Find a preceding weak draft that must not be used by the next step."""
    async with SessionLocal() as session:
        plan = await svc.get_plan(session, plan_id)
        if plan.delegation_policy not in ("balanced", "autonomous", "plan"):
            return None
        for step in svc.working_leaves(await svc.plan_steps(session, plan_id)):
            if before_step is not None and step.id == before_step:
                break
            if step.status == STEP_VALIDATED and step.needs_review:
                return step.title, "needs a new approval after its requirements changed"
            if not step.artifact.strip() or step.status == STEP_VALIDATED:
                continue
            if step.status != STEP_TO_VALIDATE:
                return step.title, "has an unfinished existing draft; it was preserved"
            if not _assessment_is_current(plan, step):
                if not ignore_unassessed:
                    return step.title, "has no current assessment"
                continue
            problems = llm.step_check_failures(step)
            weak = llm.step_weakness(step)
            if problems:
                return step.title, "failed a deliverable check: " + "; ".join(problems[:3])
            if weak and weak[1]:
                return step.title, "has unresolved assessment gaps or contradictions: " + "; ".join(
                    weak[1][:3]
                )
            if weak and weak[0] < _HOLD_BELOW:
                return step.title, f"still scores {weak[0]}/100"
    return None


def _assessment_is_current(plan: StepPlan, step: Step) -> bool:
    return (
        step.assessed_sig == svc.step_signature(plan, step)
        and svc.parse_assessment(step.assessment_json) is not None
    )


async def _validate_current_step(session: AsyncSession, plan_id: int, step: Step) -> bool:
    signature, assessment = step.assessed_sig, step.assessment_json
    if signature is None or assessment is None:
        return False
    return await svc.validate_assessed_step(
        session,
        plan_id,
        step.id,
        expected_signature=signature,
        expected_assessment=assessment,
    )


async def _ensure_step_assessed(
    session: AsyncSession, plan_id: int, plan: StepPlan, step: Step
) -> bool:
    if _assessment_is_current(plan, step):
        return True
    step_id, title = step.id, step.title
    await _note(plan_id, f"«{title}» has no current assessment. Checking it before continuing.")
    try:
        async with SessionLocal() as reviewer:
            await _retried(plan_id, llm.assess_step, reviewer, step_id)
    except Exception:
        log.exception("Assessment failed for step %s", step_id)
        await _note(
            plan_id,
            f"«{title}» could not be assessed. The draft was kept; "
            "retry the assessment or review the step yourself.",
        )
        return False
    await session.refresh(plan)
    await session.refresh(step)
    if not _assessment_is_current(plan, step):
        await _note(
            plan_id,
            f"«{step.title}» changed while it was being assessed. "
            "Review or assess the current version before continuing.",
        )
        return False
    return True


async def _assess_existing_steps(plan_id: int, *, before_step: int | None = None) -> bool:
    """Assess existing drafts without redrafting them before dependents use them."""
    async with SessionLocal() as session:
        plan = await svc.get_plan(session, plan_id)
        if plan.delegation_policy not in ("balanced", "autonomous", "plan"):
            return True
        for step in svc.working_leaves(await svc.plan_steps(session, plan_id)):
            if not await _still_running(plan_id):
                return True
            if before_step is not None and step.id == before_step:
                break
            if step.status == STEP_TO_VALIDATE and step.artifact.strip():
                if not await _ensure_step_assessed(session, plan_id, plan, step):
                    return False
                if not await _still_running(plan_id):
                    return True
                if step.status == STEP_VALIDATED:
                    continue
                if step.status != STEP_TO_VALIDATE:
                    return False
                problems = llm.step_check_failures(step)
                weak = llm.step_weakness(step)
                if problems or (weak and (weak[0] < _WEAK_BELOW or weak[1])):
                    await _note(
                        plan_id,
                        f"«{step.title}» is an existing draft that needs review. "
                        "It was preserved; review it before later steps rely on it.",
                    )
                    return False
                title = step.title
                if not await _validate_current_step(session, plan_id, step):
                    await _note(
                        plan_id,
                        f"«{title}» changed before validation. Review or assess it again.",
                    )
                    return False
                await _note(plan_id, f"«{title}» is validated before the next step starts.")
    return True


async def _review_step(session: AsyncSession, plan_id: int, plan: StepPlan, step: Step) -> bool:
    if not step.artifact.strip() or step.status != STEP_TO_VALIDATE:
        return False
    if not await _ensure_step_assessed(session, plan_id, plan, step):
        return False
    if step.status == STEP_VALIDATED:
        return True
    if step.status != STEP_TO_VALIDATE:
        return False
    step_id, title = step.id, step.title
    problems = llm.step_check_failures(step)
    weak = llm.step_weakness(step)
    if problems or (weak and (weak[0] < _WEAK_BELOW or weak[1])):
        reasons: list[str] = []
        instructions: list[str] = []
        if problems:
            reasons.append("failed a check")
            instructions.append(_FIX_FILE.format(problems="; ".join(problems)))
        if weak and (weak[0] < _WEAK_BELOW or weak[1]):
            if weak[0] < _WEAK_BELOW:
                reasons.append(f"scored {weak[0]}/100")
            elif weak[1]:
                reasons.append("has assessment gaps or contradictions")
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
            automatic=True,
        )
        if draft.outcome != "published" or not draft.step.artifact.strip():
            await _note(
                plan_id,
                f"«{title}» has no improved artifact. Pausing before drafting later steps; "
                "review this step, then resume.",
            )
            return False
        await session.refresh(plan)
        step = await svc.get_step(session, step_id)
        if not await _ensure_step_assessed(session, plan_id, plan, step):
            return False
        if step.status == STEP_VALIDATED:
            return True
        if step.status != STEP_TO_VALIDATE:
            return False
        problems = llm.step_check_failures(step)
        weak = llm.step_weakness(step)
        if problems or (weak and (weak[0] < _HOLD_BELOW or weak[1])):
            if problems:
                reason = "failed a check: " + "; ".join(problems[:3])
            elif weak and weak[1]:
                reason = "unresolved assessment gaps or contradictions: " + "; ".join(weak[1][:3])
            else:
                reason = f"still scores {weak[0] if weak else 0}/100"
            await _note(
                plan_id,
                f"«{title}» is still weak ({reason}). Pausing before later steps rely on it; "
                "review this step, then resume.",
            )
            return False
    if not await _validate_current_step(session, plan_id, step):
        await _note(plan_id, f"«{title}» changed before validation. Review or assess it again.")
        return False
    await _note(plan_id, f"«{title}» is validated before the next step starts.")
    return True


async def _review_step_before_dependents(plan_id: int, step_id: int) -> bool:
    """Repair and validate an automatic draft before later steps can rely on it."""
    async with SessionLocal() as session:
        plan = await svc.get_plan(session, plan_id)
        if plan.delegation_policy not in ("balanced", "autonomous", "plan"):
            return True
        step = await svc.get_step(session, step_id)
        reviewed = await _review_step(session, plan_id, plan, step)
    if reviewed:
        await publish_step_plan_changed(plan_id)
    return reviewed


async def _review(
    plan_id: int,
    policy: str,
    *,
    generated_final: bool = False,
    require_running: bool = False,
) -> bool:
    """Close a delegated plan as far as its topic's autonomy allows.

    Both policies validate the drafts and rebuild the final output (no longer
    provisional). ``autonomous`` also files it in the topic and wakes the topic's
    model; ``balanced`` leaves that last review to the user. ``False`` leaves the
    plan for a person to review.
    """
    try:
        if require_running and not await _still_running(plan_id):
            return False
        async with SessionLocal() as session:
            plan = await svc.get_plan(session, plan_id)
            topic_id = plan.source_topic_id
            chat_id = plan.source_chat_id
            if topic_id is None and chat_id is None:
                return False
            source = "topic" if topic_id is not None else "chat"
            steps = await svc.plan_steps(session, plan_id)
            for step in svc.working_leaves(steps):
                if require_running and not await _still_running(plan_id):
                    return False
                if step.status != STEP_TO_VALIDATE or not step.artifact.strip():
                    continue
                if not await _review_step(session, plan_id, plan, step):
                    return False
            plan = await svc.get_plan(session, plan_id)
            needs_final = (
                not (plan.final_output or "").strip() or plan.final_stale or plan.final_provisional
            )
            final_outcome = "published"
            if needs_final:
                if (plan.final_output or "").strip() and not generated_final:
                    await _note(
                        plan_id,
                        "The existing final output needs review and was preserved. "
                        "Review or regenerate it before continuing.",
                    )
                    return False
                final = await _retried(
                    plan_id, llm.generate_final, session, plan_id, force=True, automatic=True
                )
                if require_running and not await _still_running(plan_id):
                    return False
                if final.outcome == "discussion":
                    final = await _retried(
                        plan_id,
                        llm.generate_final,
                        session,
                        plan_id,
                        force=True,
                        instructions=_NO_QUESTIONS,
                        automatic=True,
                    )
                    if require_running and not await _still_running(plan_id):
                        return False
                final_outcome = final.outcome
                generated_final = final_outcome == "published"
            if final_outcome == "published":
                problems, built = await llm.final_file_problems(session, plan_id)
                if problems:
                    if not generated_final:
                        await _note(
                            plan_id,
                            "The existing final output has a file problem "
                            f"({'; '.join(problems)}). It was preserved; review it yourself.",
                        )
                        return False
                    if require_running and not await _still_running(plan_id):
                        return False
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
                        automatic=True,
                    )
                    if require_running and not await _still_running(plan_id):
                        return False
                    final_outcome = final.outcome
                    problems, _ = await llm.final_file_problems(session, plan_id)
                    if problems:
                        await _note(
                            plan_id,
                            "The final output still has a file problem. It was "
                            f"not filed in the {source}: review it, then send it yourself.",
                        )
                        return False
            if final_outcome != "published":
                await _note(
                    plan_id,
                    "The model did not write the final output. Regenerate it from the plan, "
                    f"then send it to the {source}.",
                )
                return False
            repaired_gaps = False
            while True:
                if require_running and not await _still_running(plan_id):
                    return False
                plan = await svc.get_plan(session, plan_id)
                await session.refresh(plan)
                verdict = svc.parse_assessment(plan.assessment_json)
                steps = await svc.plan_steps(session, plan_id, refresh=True)
                if (
                    plan.final_stale
                    or plan.final_provisional
                    or not svc.is_ready(steps)
                    or (plan.source_topic_id, plan.source_chat_id) != (topic_id, chat_id)
                ):
                    await _note(
                        plan_id,
                        "The final output or its source changed during review. "
                        "It was not filed; review the current plan before continuing.",
                    )
                    return False
                if (
                    verdict is None
                    or verdict.scope != "final"
                    or plan.assessed_sig != svc.plan_signature(plan, steps)
                ):
                    await _retried(plan_id, llm.assess_plan, session, plan_id)
                    if require_running and not await _still_running(plan_id):
                        return False
                    plan = await svc.get_plan(session, plan_id)
                    await session.refresh(plan)
                    verdict = svc.parse_assessment(plan.assessment_json)
                    steps = await svc.plan_steps(session, plan_id, refresh=True)
                    if (
                        verdict is None
                        or verdict.scope != "final"
                        or plan.assessed_sig != svc.plan_signature(plan, steps)
                        or plan.final_stale
                        or plan.final_provisional
                        or not svc.is_ready(steps)
                        or (plan.source_topic_id, plan.source_chat_id) != (topic_id, chat_id)
                    ):
                        await _note(
                            plan_id,
                            "The final output has no current assessment. "
                            "It was not filed; assess the current version or review it yourself.",
                        )
                        return False
                assert verdict is not None
                gaps = [
                    item.strip()
                    for item in [*verdict.gaps, *verdict.contradictions]
                    if item.strip()
                ]
                if not gaps:
                    break
                if not generated_final or repaired_gaps:
                    await _note(
                        plan_id,
                        "The final assessment still has unresolved gaps or contradictions "
                        f"({'; '.join(gaps[:3])}). The output was preserved and not filed "
                        f"in the {source}; review it, then send it yourself.",
                    )
                    return False
                await _note(
                    plan_id,
                    "The final assessment identified gaps or contradictions. "
                    "Rebuilding the final output once before filing.",
                )
                if require_running and not await _still_running(plan_id):
                    return False
                final = await _retried(
                    plan_id,
                    llm.generate_final,
                    session,
                    plan_id,
                    force=True,
                    instructions=_NO_QUESTIONS + _FIX_FINAL_GAPS.format(gaps="; ".join(gaps[:5])),
                    automatic=True,
                )
                if require_running and not await _still_running(plan_id):
                    return False
                if final.outcome == "awaiting_approval":
                    await _note(
                        plan_id,
                        "Paused: a tool call while rebuilding the final output needs your approval.",
                    )
                    await _set_state(plan_id, AUTO_PAUSED)
                    return False
                if final.outcome != "published":
                    await _note(
                        plan_id,
                        "The final output could not be rebuilt. "
                        f"It was not filed in the {source}; review it, then send it yourself.",
                    )
                    return False
                problems, _ = await llm.final_file_problems(session, plan_id)
                if problems:
                    await _note(
                        plan_id,
                        "The rebuilt final output has a file problem "
                        f"({'; '.join(problems)}). It was not filed in the {source}; review it.",
                    )
                    return False
                repaired_gaps = True
            if verdict.score < _HOLD_BELOW:
                await _note(
                    plan_id,
                    f"The final output scored {verdict.score}/100 against the ask "
                    f"({'; '.join(verdict.gaps[:3]) or 'no gaps listed'}). It was not filed "
                    f"in the {source}: review it, then send it yourself.",
                )
                return False
            posted_message_id = None
            if policy == "autonomous":
                if require_running and not await _still_running(plan_id):
                    return False
                if plan.assessed_sig is None or plan.assessment_json is None:
                    await _note(plan_id, "The final output has no current assessment.")
                    return False
                plan, posted = await llm.send_to_source(
                    session,
                    plan_id,
                    None,
                    assessed=llm.AssessedFinal(
                        signature=plan.assessed_sig,
                        assessment_json=plan.assessment_json,
                        source_topic_id=topic_id,
                        source_chat_id=chat_id,
                        version=plan.final_version,
                        require_running=require_running,
                    ),
                )
                posted_message_id = posted.id
            title = plan.title
            final_version = plan.final_version
        await publish_step_plan_changed(plan_id)
        if policy == "autonomous":
            await _note(
                plan_id,
                f"Drafts validated and the final output filed in the {source}, which now reviews it.",
            )
            if topic_id is not None:
                hand_back(
                    topic_id,
                    title,
                    reviewed_by_user=False,
                    plan_id=plan_id,
                    posted_message_id=posted_message_id,
                    final_version=final_version,
                )
            elif chat_id is not None:
                hand_back(
                    chat_id,
                    title,
                    reviewed_by_user=False,
                    source_kind="chat",
                    plan_id=plan_id,
                    posted_message_id=posted_message_id,
                    final_version=final_version,
                )
        else:
            await _note(
                plan_id,
                "Drafts validated. The final output is ready: review it, then send it to "
                f"the {source}.",
            )
    except Exception as exc:
        if require_running and not await _still_running(plan_id):
            return False
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        log.exception("Review of step plan %s failed", plan_id)
        await _note(plan_id, f"Automatic review stopped, please review the plan yourself: {detail}")
        return False
    return True


async def _start_locked(session: AsyncSession, plan_id: int) -> StepPlan:
    plan = await svc.get_plan(session, plan_id)
    await session.refresh(plan)
    svc.require_unarchived(plan)
    # A delegated plan may have been made under a looser source policy since.
    if plan.delegation_policy is not None:
        await svc.sync_delegation_policy(session, plan)
    if plan.automation_mode != AUTOMATION_AUTO:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Switch the plan to fully automated to run it."
        )
    if plan.status not in (PLAN_ACTIVE, PLAN_CONSOLIDATING):
        raise HTTPException(status.HTTP_409_CONFLICT, "Start the plan first.")
    if plan.auto_state == AUTO_PAUSED and plan_id not in svc.RUNNING:
        unresolved = await _unresolved_step(plan_id, ignore_unassessed=True)
        if unresolved is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"«{unresolved[0]}» {unresolved[1]}. Review this step before resuming.",
            )
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


async def start(session: AsyncSession, plan_id: int) -> StepPlan:
    async with svc.plan_lock(plan_id):
        return await _start_locked(session, plan_id)


async def activate(session: AsyncSession, plan_id: int, *, accept_proposed: bool) -> StepPlan:
    async with svc.plan_lock(plan_id):
        plan = await svc.get_plan(session, plan_id)
        await session.refresh(plan)
        svc.require_unarchived(plan)
        plan = await svc.start_plan(session, plan_id, accept_proposed=accept_proposed)
        if plan.automation_mode == AUTOMATION_AUTO:
            return await _start_locked(session, plan_id)
        return plan


async def launch(session: AsyncSession, plan_id: int) -> StepPlan:
    async with svc.plan_lock(plan_id):
        plan = await svc.get_plan(session, plan_id)
        await session.refresh(plan)
        svc.require_unarchived(plan)
        if plan.automation_mode != AUTOMATION_AUTO:
            await svc.update_plan(session, plan_id, StepPlanUpdate(automation_mode="auto"))
        if plan.status == PLAN_DRAFTING:
            await svc.start_plan(session, plan_id, accept_proposed=True)
        return await _start_locked(session, plan_id)


async def pause(session: AsyncSession, plan_id: int) -> StepPlan:
    """Stop after the step in flight; the run resumes with ``start``."""
    async with svc.plan_lock(plan_id):
        await session.execute(
            update(StepPlan)
            .where(
                StepPlan.id == plan_id,
                StepPlan.auto_state == AUTO_RUNNING,
                StepPlan.status.in_((PLAN_ACTIVE, PLAN_CONSOLIDATING)),
            )
            .values(auto_state=AUTO_PAUSED)
        )
        await session.commit()
        plan = await svc.get_plan(session, plan_id)
        await session.refresh(plan)
        return plan


async def join(plan_id: int) -> None:
    """Wait for a plan's run to end (tests, shutdown)."""
    task = _TASKS.get(plan_id)
    if task is not None:
        await asyncio.shield(task)
