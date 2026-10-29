"""The model-driven actions of a step-by-step plan.

Structured actions (suggesting steps, reviewing the plan) are explicit,
one-shot, tool-less calls (``complete_once``). Drafting an artifact or the final
output is a real turn of the step's (or the consolidation) chat, run through the
shared headless pipeline, so it is tracked in that discussion and inherits the
chat's model, role, tools and approval gates. A reply is a discussion: only the
deliverable the model marks (``ARTIFACT_OPEN`` … ``ARTIFACT_CLOSE``) becomes an
artifact version, always landing in ``to_validate`` — the model never validates.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import Chat, Message, MessageRole, Step, StepPlan
from precursor.backend.models.step_plan import (
    AUTHOR_MODEL,
    AUTOMATION_MANUAL,
    PLAN_ACTIVE,
    PLAN_CONSOLIDATING,
    PLAN_DONE,
    PLAN_DRAFTING,
    STEP_CANCELLED,
    STEP_TO_VALIDATE,
    STEP_VALIDATED,
)
from precursor.backend.schemas.conversation_llm import ConversationLLMSelection
from precursor.backend.schemas.message import MessageRead
from precursor.backend.schemas.step_plan import Assessment, StepCreate
from precursor.backend.services import step_plans as svc
from precursor.backend.services.compaction import render_transcript
from precursor.backend.services.conversation_turn import load_model_history
from precursor.backend.services.events import (
    broadcast_events,
    publish_message_changed,
    publish_message_changed_chat,
    publish_step_plan_changed,
)
from precursor.backend.services.llm.one_shot import complete_once
from precursor.backend.services.turn import run_chat_turn

from sqlalchemy import select  # isort: skip

log = logging.getLogger(__name__)

USAGE_SOURCE = "/step-by-step"
_DISCUSSION_CAP = 40_000
_MAX_PROPOSED = 40
_TITLE_CHARS = 120

_LANGUAGE = "Write in the language of the original ask."

_SPLIT_SYSTEM = f"""\
You break a long ask (an email, a brief, a list of requests) into the work \
steps needed to answer it. Reply with JSON only, no prose and no code fence:
{{"steps": [{{"title": "...", "instruction": "...", "children": []}}]}}

Rules:
- One step per distinct request or deliverable, in a sensible working order; \
usually 3 to 12 top-level steps.
- "title": a short label, at most 80 characters.
- "instruction": what the step must produce, keeping the specifics (names, \
figures, constraints) from the ask. Never invent requests that aren't there.
- Only add "children" when a request clearly splits into independent parts \
that deserve their own step; otherwise leave it empty.
- {_LANGUAGE}"""

_MISSING_SYSTEM = f"""\
You check whether a plan covers every request of an ask. You get the ask and \
the steps already planned. Reply with JSON only, no prose and no code fence, \
listing ONLY steps for requests the plan does not cover yet:
{{"steps": [{{"title": "...", "instruction": "...", "children": []}}]}}

Return {{"steps": []}} when nothing is missing. Never repeat or rephrase an \
existing step, and never invent requests that aren't in the ask. {_LANGUAGE}"""

_SUBSTEPS_SYSTEM = f"""\
You split one step of a plan into smaller steps that can be worked and \
validated independently. Reply with JSON only, no prose and no code fence:
{{"steps": [{{"title": "...", "instruction": "...", "children": []}}]}}

Return 2 to 6 steps that together cover the step completely, without overlap, \
in a sensible order. Keep the specifics of the original step. {_LANGUAGE}"""

_ARTIFACT_DRAFT_PROMPT = f"""\
Draft the result (the "artifact") of this step now, or revise the current one. \
It will be reviewed by the user and later combined with the other steps' \
artifacts into one deliverable.

- Use this discussion as the source of truth for decisions and wording: what \
the user and you settled on wins over your own preferences.
- Ground everything in the original ask and the discussion; never invent \
facts, figures or commitments.
- Put the complete artifact between a line `{svc.ARTIFACT_OPEN}` and a line \
`{svc.ARTIFACT_CLOSE}`: clean Markdown, ready to be reused as is, no preamble \
and no code fence inside the markers. {_LANGUAGE}
- Anything else (questions you need answered, alternatives, caveats) goes \
OUTSIDE the markers, briefly. If you can't write a trustworthy artifact yet, \
ask your questions and leave the markers out."""

_REVIEW_SYSTEM = """\
You review a step-by-step plan before it is consolidated. Compare the original \
ask with the steps and their artifacts and report, as short Markdown bullets \
under these headings (skip a heading that has nothing to say):

**Not covered** — requests of the ask no step handles.
**Contradictions** — places where two steps disagree (name both).
**Weak or empty** — validated or drafted steps that don't really answer their \
instruction.
**Suggested next move** — one sentence.

Be specific and brief; quote step titles. Say so plainly when the plan looks \
sound. Write in the language of the original ask."""

_SELF_ASSESSMENT = """\
Be a strict, honest reviewer of the work, not its cheerleader. Your score is a \
self-assessment, not a measured probability: 90+ only when every requirement is \
clearly covered and supported; below 50 when something essential is missing or \
unsupported. Never reward length or confident tone. Facts that appear in neither \
the ask nor the material are assumptions, not evidence."""

_ASSESS_STEP_SYSTEM = f"""\
You assess how well ONE step's artifact answers what that step asked for, in the \
context of the original ask. {_SELF_ASSESSMENT}

Reply with JSON only, no prose and no code fence:
{{"score": 0-100, "summary": "one or two sentences", "covered": ["requirement \
addressed", "..."], "evidence": ["where the support comes from: a quoted line, a \
source, a figure from the ask; leave empty if there is none"], "assumptions": \
["unverified statement the artifact relies on"], "gaps": ["requirement not \
covered, missing fact or open question"]}}

Each list holds short items (under 160 characters), at most 8. "covered" and \
"gaps" must refer to the requirements of the step's instruction and of the ask \
it serves. {_LANGUAGE}"""

_ASSESS_PLAN_SYSTEM = f"""\
You assess the WHOLE result of a step-by-step plan against the original ask: \
does the set of artifacts answer every request, and does it hold together? \
{_SELF_ASSESSMENT}

Do NOT average the steps' scores. Go through the original ask request by \
request, then look across the steps for omissions and contradictions.

Reply with JSON only, no prose and no code fence:
{{"score": 0-100, "summary": "one or two sentences", "covered": ["request of the \
ask that is answered", "..."], "evidence": ["which step(s) support it"], \
"assumptions": ["unverified statement the result relies on"], "gaps": ["request \
of the ask that no artifact answers, or answers only in part"], "contradictions": \
["two steps that disagree: name both and the point"]}}

Each list holds short items (under 200 characters), at most 10. Steps without an \
artifact count as gaps. {_LANGUAGE}"""

_FINAL_DRAFT_PROMPT = f"""\
Consolidate the plan into ONE final deliverable now.

- Answer the original ask completely, using the artifacts as your material; \
keep their decisions, numbers, names and commitments verbatim.
- Smooth the transitions and remove repetition, but never add content that \
isn't supported by the artifacts, and never drop an artifact's substance.
- Follow the requested output (format, tone, length) when one is given; \
otherwise write a clear, well-structured Markdown answer.
- If artifacts contradict each other, keep the later step's version and list \
the conflict at the end under "Points to check".
- Put the complete deliverable between a line `{svc.ARTIFACT_OPEN}` and a line \
`{svc.ARTIFACT_CLOSE}`, with no preamble and no code fence inside the markers. \
{_LANGUAGE}
- Anything else (questions, caveats) goes OUTSIDE the markers, briefly. If you \
can't consolidate yet, ask and leave the markers out."""


# --- plumbing -----------------------------------------------------------------


def _selection_kwargs(plan: StepPlan) -> dict[str, Any]:
    """Model overrides for a plan with a pinned model (a preset category falls back
    to the default model, as a one-shot call has no conversation to resolve it)."""
    if not plan.llm_selection:
        return {}
    selection = ConversationLLMSelection.model_validate_json(plan.llm_selection)
    if not selection.model:
        return {}
    return {
        "model": selection.model,
        "provider_id": selection.provider,
        "reasoning_effort": selection.reasoning_effort or None,
    }


async def _complete(session: AsyncSession, plan: StepPlan, *, system: str, user: str) -> str:
    result = await complete_once(
        session,
        system=system,
        user=user,
        usage_source=USAGE_SOURCE,
        topic_id=plan.source_topic_id,
        use_conversation_selection=False,
        **_selection_kwargs(plan),
    )
    return result.text.strip()


def extract_json(text: str) -> Any:
    """The first JSON object or array in ``text`` (models fence or preface it)."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                value, _ = decoder.raw_decode(text[i:])
            except ValueError:
                continue
            return value
    return None


def parse_steps(text: str) -> list[dict[str, Any]]:
    data = extract_json(text)
    items = data.get("steps") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "The model didn't return a usable list of steps. Try again.",
        )
    return _clean(items, depth=0)


def _clean(items: list[Any], *, depth: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            item = {"title": item}
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:_TITLE_CHARS]
        if not title:
            continue
        children = item.get("children")
        out.append(
            {
                "title": title,
                "instruction": str(item.get("instruction") or "").strip(),
                "children": (
                    _clean(children, depth=depth + 1)
                    if isinstance(children, list) and depth + 1 < svc.MAX_DEPTH - 2
                    else []
                ),
            }
        )
    return out


async def _discussion(session: AsyncSession, chat_id: int | None) -> str:
    if chat_id is None:
        return ""
    history = await load_model_history(session, "chat", chat_id)
    blocks = render_transcript([m for m in history if m.role in ("user", "assistant")])
    text = "\n\n".join(blocks)
    if len(text) > _DISCUSSION_CAP:
        text = "…(earlier messages omitted)\n" + text[-_DISCUSSION_CAP:]
    return text


async def _chat_id(
    session: AsyncSession, *, step_id: int | None = None, plan_id: int | None = None
) -> int | None:
    stmt = select(Chat.id).order_by(Chat.id).limit(1)
    stmt = (
        stmt.where(Chat.step_id == step_id)
        if step_id is not None
        else stmt.where(Chat.step_plan_id == plan_id)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# --- suggesting steps ---------------------------------------------------------


async def _insert(
    session: AsyncSession,
    plan: StepPlan,
    items: list[dict[str, Any]],
    parent_id: int | None,
    budget: list[int],
) -> None:
    for item in items:
        if budget[0] <= 0:
            return
        budget[0] -= 1
        step = await svc.create_step(
            session,
            plan,
            StepCreate(
                title=item["title"], instruction=item["instruction"], parent_step_id=parent_id
            ),
            author=AUTHOR_MODEL,
            proposed=True,
            commit=False,
        )
        await _insert(session, plan, item["children"], step.id, budget)


async def suggest_steps(
    session: AsyncSession,
    plan_id: int,
    *,
    mode: str,
    parent_step_id: int | None = None,
    instructions: str | None = None,
) -> int:
    """Have the model suggest steps (``initial``, ``missing`` or ``substeps``).

    New suggestions replace the previous, unaccepted ones (all of them, or the
    ones under ``parent_step_id``). Returns how many were added.
    """
    plan = await svc.get_plan(session, plan_id)
    steps = await svc.plan_steps(session, plan.id)
    accepted = [s for s in steps if s.status != "proposed"]
    parent: Step | None = None
    if mode == "substeps":
        parent = next((s for s in steps if s.id == parent_step_id), None)
        if parent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Step not found")
        if parent.artifact.strip():
            raise HTTPException(
                status.HTTP_409_CONFLICT, "This step already has an artifact; it can't be split."
            )
        system = _SUBSTEPS_SYSTEM
        user = (
            f"{svc.plan_header(plan)}\n\nPlan outline:\n{svc.render_outline(accepted, mark=parent.id)}"
            f"\n\nStep to split: {parent.title}\n{parent.instruction}"
        )
    elif mode == "missing":
        system = _MISSING_SYSTEM
        user = f"{svc.plan_header(plan)}\n\nSteps already planned:\n{svc.render_outline(accepted) or '(none)'}"
    else:
        system = _SPLIT_SYSTEM
        user = svc.plan_header(plan)
        if accepted:
            user += f"\n\nSteps the user already has (don't repeat them):\n{svc.render_outline(accepted)}"
    focus = (instructions or "").strip()
    if focus:
        user += f"\n\nExtra guidance from the user: {focus}"

    items = parse_steps(await _complete(session, plan, system=system, user=user))
    await svc.drop_proposed(session, plan, parent_id=parent.id if parent else None)
    budget = [_MAX_PROPOSED]
    await _insert(session, plan, items, parent.id if parent else None, budget)
    await session.commit()
    return _MAX_PROPOSED - budget[0]


# --- artifacts ----------------------------------------------------------------


DraftOutcome = Literal["published", "discussion", "awaiting_approval"]


@dataclass
class ArtifactDraft:
    step: Step
    outcome: DraftOutcome


@dataclass
class FinalDraft:
    plan: StepPlan
    outcome: DraftOutcome


def _visible_prompt(base: str, instructions: str | None) -> str:
    focus = (instructions or "").strip()
    return f"{base}\n\n{focus}" if focus else base


def _llm_prompt(rules: str, instructions: str | None) -> str:
    focus = (instructions or "").strip()
    return f"{rules}\n\nExtra guidance from the user: {focus}" if focus else rules


async def generate_artifact(
    session: AsyncSession, step_id: int, *, instructions: str | None = None
) -> ArtifactDraft:
    """Ask the step's discussion to draft the artifact.

    The turn is tracked in the step's chat. Only a deliverable the model marked
    is saved (as a ``to_validate`` version); a reply with questions or
    alternatives stays in the discussion.
    """
    step = await svc.get_step(session, step_id)
    steps = await svc.plan_steps(session, step.plan_id)
    if any(s.parent_step_id == step.id and s.status != "proposed" for s in steps):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This step is split into sub-steps; it has no artifact."
        )
    chat = await svc.ensure_step_chat(session, step.id)
    with broadcast_events():
        result = await run_chat_turn(
            chat.id,
            _visible_prompt("Draft the artifact for this step.", instructions),
            llm_prompt=_llm_prompt(_ARTIFACT_DRAFT_PROMPT, instructions),
        )
    session.expire_all()
    if result.awaiting_approval:
        return ArtifactDraft(await svc.get_step(session, step_id), "awaiting_approval")
    deliverable = svc.extract_deliverable(result.reply or "")
    if deliverable is None:
        return ArtifactDraft(await svc.get_step(session, step_id), "discussion")
    saved = await svc.write_artifact(
        session, step_id, deliverable, author=AUTHOR_MODEL, mark=STEP_TO_VALIDATE
    )
    # The draft is on screen before the (slower) assessment starts.
    with broadcast_events():
        await publish_step_plan_changed(saved.plan_id)
    await _assess_after_draft(session, saved)
    return ArtifactDraft(saved, "published")


async def _chat_message(session: AsyncSession, chat_id: int | None, message_id: int) -> str:
    msg = await session.get(Message, message_id)
    if chat_id is None or msg is None or msg.chat_id != chat_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found in this discussion.")
    if msg.role != MessageRole.ASSISTANT or not msg.content.strip():
        raise HTTPException(status.HTTP_409_CONFLICT, "Pick one of the assistant's replies.")
    return msg.content


async def use_message_as_artifact(session: AsyncSession, step_id: int, message_id: int) -> Step:
    """Publish a reply the user picked: its marked deliverable, else all of it."""
    chat_id = await _chat_id(session, step_id=step_id)
    content = await _chat_message(session, chat_id, message_id)
    text = svc.extract_deliverable(content) or content.strip()
    current = await svc.get_step(session, step_id)
    if current.artifact.strip("\n") == text.strip("\n"):
        # Sending what the artifact already holds (an old card clicked again)
        # must not demote a validated step.
        return current
    saved = await svc.write_artifact(
        session, step_id, text, author=AUTHOR_MODEL, mark=STEP_TO_VALIDATE
    )
    await _assess_after_draft(session, saved)
    return saved


_background: set[asyncio.Task[None]] = set()


def _spawn(coro: Any) -> None:
    task = asyncio.ensure_future(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _assess_later(step_id: int | None, plan_id: int) -> None:
    """Score a discussion edit off the request path: it's a slow model call."""
    with broadcast_events():
        async with SessionLocal() as session:
            if step_id is not None:
                step = await svc.get_step(session, step_id)
                await _assess_after_draft(session, step)
            else:
                await _assess_plan_quietly(session, await svc.get_plan(session, plan_id))
        await publish_step_plan_changed(plan_id)


async def apply_chat_reply(chat_id: int, reply: str) -> None:
    """Apply the deliverable of a live discussion reply to the artifact.

    Called once an interactive reply in a step's (or the consolidation) chat is
    saved, so asking the model in the discussion to write or change the
    artifact takes effect without a separate click. Only a marked deliverable
    counts; it lands as a new unvalidated version, so the model still never
    approves anything, and a validated artifact is left alone (the user
    reopens the step first). Never raises: the reply is already saved.
    """
    deliverable = svc.extract_deliverable(reply)
    if deliverable is None:
        return
    try:
        async with SessionLocal() as session:
            chat = await session.get(Chat, chat_id)
            if chat is None or (chat.step_id is None and chat.step_plan_id is None):
                return
            if chat.step_id is not None:
                plan_id = await _apply_to_step(session, chat_id, chat.step_id, deliverable)
                step_id: int | None = chat.step_id
            else:
                assert chat.step_plan_id is not None
                plan_id = chat.step_plan_id
                await save_final(session, plan_id, deliverable)
                await trace_in_chat(
                    session, chat_id, "Saved as the consolidated draft (from this reply)."
                )
                step_id = None
        if plan_id is not None:
            await publish_step_plan_changed(plan_id)
            _spawn(_assess_later(step_id, plan_id))
    except HTTPException as exc:
        async with SessionLocal() as session:
            await trace_in_chat(session, chat_id, f"Not applied to the artifact: {exc.detail}")
    except Exception:
        log.exception("Applying a discussion reply failed for chat %s", chat_id)


async def _apply_to_step(
    session: AsyncSession, chat_id: int, step_id: int, deliverable: str
) -> int | None:
    step = await svc.get_step(session, step_id)
    if step.status == STEP_VALIDATED:
        await trace_in_chat(
            session,
            chat_id,
            "Not applied: this step is validated. Reopen it to let the discussion "
            "change its artifact.",
        )
        return None
    if step.status == STEP_CANCELLED:
        return None
    before = step.artifact_version
    saved = await svc.write_artifact(
        session, step_id, deliverable, author=AUTHOR_MODEL, mark=STEP_TO_VALIDATE
    )
    if saved.artifact_version == before:
        await trace_in_chat(session, chat_id, "This reply matches the current artifact: unchanged.")
        return None
    await trace_in_chat(
        session,
        chat_id,
        f"Applied to the artifact as version {saved.artifact_version}, to validate. "
        "Your unsaved edits in the editor are kept.",
    )
    return saved.plan_id


async def review_plan(session: AsyncSession, plan_id: int) -> str:
    plan = await svc.get_plan(session, plan_id)
    steps = await svc.plan_steps(session, plan.id)
    artifacts = svc.render_artifacts(steps, include_unvalidated=True)
    user = f"{svc.plan_header(plan)}\n\nPlan outline:\n{svc.render_outline(steps)}"
    if artifacts:
        user += f"\n\nSteps' artifacts:\n<<<ARTIFACTS\n{artifacts}\nARTIFACTS>>>"
    return await _complete(session, plan, system=_REVIEW_SYSTEM, user=user)


# --- assessments --------------------------------------------------------------

_ITEM_CAP = 300
_LIST_CAP = 10


def _items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out = [str(v).strip()[:_ITEM_CAP] for v in value if str(v).strip()]
    return out[:_LIST_CAP]


def parse_assessment(text: str) -> Assessment:
    data = extract_json(text)
    if not isinstance(data, dict):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "The model didn't return a usable assessment. Try again."
        )
    try:
        score = round(float(data.get("score") or 0))
    except (TypeError, ValueError):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "The model didn't return a score. Try again."
        ) from None
    return Assessment(
        score=max(0, min(100, score)),
        summary=str(data.get("summary") or "").strip()[:600],
        covered=_items(data.get("covered")),
        evidence=_items(data.get("evidence")),
        assumptions=_items(data.get("assumptions")),
        gaps=_items(data.get("gaps")),
        contradictions=_items(data.get("contradictions")),
    )


def _assessment_note(subject: str, a: Assessment, *, version: int | None = None) -> str:
    where = f" of version {version}" if version else ""
    lines = [
        f"Model self-assessment{where} — {subject}: **{a.score}/100** "
        "(an estimate by the model, not a measured probability)."
    ]
    if a.summary:
        lines.append(a.summary)
    if a.gaps:
        lines.append("Gaps: " + "; ".join(a.gaps))
    if a.contradictions:
        lines.append("Contradictions: " + "; ".join(a.contradictions))
    return "\n\n".join(lines)


async def trace_in_chat(session: AsyncSession, chat_id: int | None, text: str) -> None:
    """Leave a UI-only note in a plan chat so an automated action stays traceable."""
    if chat_id is None:
        return
    session.add(
        Message(
            chat_id=chat_id,
            role=MessageRole.SYSTEM,
            kind=svc.STEP_PLAN_TRACE_KIND,
            content=text,
        )
    )
    await session.commit()
    with broadcast_events():
        await publish_message_changed_chat(chat_id)


async def _assess_or_trace(
    session: AsyncSession, plan: StepPlan, system: str, user: str, chat_id: int | None
) -> Assessment:
    """Run an assessment call; a failure closes the "Assessing…" note in the chat."""
    try:
        return parse_assessment(await _complete(session, plan, system=system, user=user))
    except Exception:
        await trace_in_chat(session, chat_id, "The assessment didn't complete. Try it again.")
        raise


async def assess_step(session: AsyncSession, step_id: int) -> Step:
    """Have the model assess one step's artifact; the score is tied to that content."""
    step = await svc.get_step(session, step_id)
    plan = await svc.get_plan(session, step.plan_id)
    steps = await svc.plan_steps(session, plan.id)
    if not step.artifact.strip():
        raise HTTPException(status.HTTP_409_CONFLICT, "There's no artifact to assess yet.")
    if any(s.parent_step_id == step.id and s.status != "proposed" for s in steps):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This step is split into sub-steps; assess those instead."
        )
    user = (
        f"{svc.plan_header(plan)}\n\nPlan outline:\n{svc.render_outline(steps, mark=step.id)}"
        f"\n\nStep: {step.title}\nWhat it must produce:\n{step.instruction or '(no instruction)'}"
        f"\n\nIts artifact:\n<<<ARTIFACT\n{step.artifact}\nARTIFACT>>>"
    )
    sig = svc.step_signature(plan, step)
    await trace_in_chat(
        session,
        await _chat_id(session, step_id=step.id),
        f"Assessing «{step.title}» (version {step.artifact_version}): the model checks the "
        "artifact against what this step must produce…",
    )
    assessment = await _assess_or_trace(
        session, plan, _ASSESS_STEP_SYSTEM, user, await _chat_id(session, step_id=step.id)
    )
    step = await svc.get_step(session, step_id)
    step.assessment_json = assessment.model_dump_json()
    step.assessed_sig = sig
    await session.commit()
    await trace_in_chat(
        session,
        await _chat_id(session, step_id=step.id),
        _assessment_note(step.title, assessment, version=step.artifact_version),
    )
    return step


async def assess_plan(session: AsyncSession, plan_id: int) -> StepPlan:
    """Have the model assess the whole result against the original ask."""
    plan = await svc.get_plan(session, plan_id)
    steps = await svc.plan_steps(session, plan.id)
    leaves = svc.working_leaves(steps)
    if not any(s.artifact.strip() for s in leaves):
        raise HTTPException(status.HTTP_409_CONFLICT, "No step has a result to assess yet.")
    blocks = []
    for s in leaves:
        state = "validated" if svc.is_approved(s) else f"NOT validated ({s.status})"
        body = s.artifact.strip() or "(no artifact yet)"
        blocks.append(f"### {s.title} [{state}]\nAsked: {s.instruction or '-'}\n\n{body}")
    user = f"{svc.plan_header(plan)}\n\nSteps and their artifacts:\n" + "\n\n".join(blocks)
    sig = svc.plan_signature(plan, steps)
    await trace_in_chat(
        session,
        await _chat_id(session, plan_id=plan.id),
        "Assessing the whole plan: the model checks the result against the original ask…",
    )
    assessment = await _assess_or_trace(
        session, plan, _ASSESS_PLAN_SYSTEM, user, await _chat_id(session, plan_id=plan.id)
    )
    plan = await svc.get_plan(session, plan_id)
    plan.assessment_json = assessment.model_dump_json()
    plan.assessed_sig = sig
    await session.commit()
    await trace_in_chat(
        session,
        await _chat_id(session, plan_id=plan.id),
        _assessment_note("whole plan", assessment),
    )
    return plan


async def _assess_after_draft(session: AsyncSession, step: Step) -> None:
    """In assisted and automated plans a model draft is scored right away.

    A failed assessment never loses the draft: it just stays unassessed.
    """
    plan = await svc.get_plan(session, step.plan_id)
    if plan.automation_mode == AUTOMATION_MANUAL:
        return
    try:
        await assess_step(session, step.id)
    except HTTPException as exc:
        log.warning("Step assessment failed for step %s: %s", step.id, exc.detail)
    except Exception:
        log.exception("Step assessment failed for step %s", step.id)


# --- consolidation ------------------------------------------------------------


async def generate_final(
    session: AsyncSession,
    plan_id: int,
    *,
    instructions: str | None = None,
    force: bool = False,
    complete: bool = False,
) -> FinalDraft:
    """Ask the consolidation discussion for the final output.

    With every working step validated the output is built from approved
    artifacts only. Consolidating anyway also draws on unvalidated drafts and
    the result is flagged provisional: a draft for review, not an approved one.
    """
    plan = await svc.get_plan(session, plan_id)
    if plan.status == PLAN_DRAFTING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Start the plan first.")
    steps = await svc.plan_steps(session, plan.id)
    leaves = svc.working_leaves(steps)
    pending = [s for s in leaves if not svc.is_approved(s)]
    if pending and not force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{len(pending)} step(s) aren't validated yet. Validate or cancel them, "
            "or consolidate anyway (the result stays provisional).",
        )
    if not any(s.artifact.strip() for s in leaves if force or svc.is_approved(s)):
        raise HTTPException(status.HTTP_409_CONFLICT, "No step has a result to consolidate yet.")
    rules = _FINAL_DRAFT_PROMPT
    if pending:
        rules += (
            "\n\nSome steps are NOT validated yet: use their drafts too, and list "
            'those steps under "Points to check" as unvalidated.'
        )
    else:
        rules += "\n\nUse the validated artifacts only."
    chat = await svc.ensure_consolidation_chat(session, plan.id)
    with broadcast_events():
        result = await run_chat_turn(
            chat.id,
            _visible_prompt("Consolidate the plan into the final deliverable.", instructions),
            llm_prompt=_llm_prompt(rules, instructions),
        )
    session.expire_all()
    if result.awaiting_approval:
        return FinalDraft(await svc.get_plan(session, plan_id), "awaiting_approval")
    deliverable = svc.extract_deliverable(result.reply or "")
    if deliverable is None:
        return FinalDraft(await svc.get_plan(session, plan_id), "discussion")
    saved = await save_final(session, plan_id, deliverable, complete=complete)
    await _assess_plan_quietly(session, saved)
    return FinalDraft(saved, "published")


async def _assess_plan_quietly(session: AsyncSession, plan: StepPlan) -> None:
    if plan.automation_mode == AUTOMATION_MANUAL:
        return
    try:
        await assess_plan(session, plan.id)
    except HTTPException as exc:
        log.warning("Plan assessment failed for plan %s: %s", plan.id, exc.detail)
    except Exception:
        log.exception("Plan assessment failed for plan %s", plan.id)


async def use_message_as_final(
    session: AsyncSession, plan_id: int, message_id: int, *, complete: bool = False
) -> StepPlan:
    chat_id = await _chat_id(session, plan_id=plan_id)
    content = await _chat_message(session, chat_id, message_id)
    return await save_final(
        session, plan_id, svc.extract_deliverable(content) or content.strip(), complete=complete
    )


async def save_final(
    session: AsyncSession, plan_id: int, content: str, *, complete: bool = False
) -> StepPlan:
    """Keep ``content`` as the final output.

    ``complete`` is for a user's own request: an output built from approved
    steps then finishes the plan. The automated run and discussion edits never
    pass it, so a model can't finish a plan on its own.
    """
    plan = await svc.get_plan(session, plan_id)
    if plan.status == PLAN_DRAFTING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Start the plan first.")
    steps = await svc.plan_steps(session, plan.id)
    plan.final_output = content.strip("\n") or None
    plan.final_stale = False
    # Built while a working step is unapproved: a draft, not an approved result.
    plan.final_provisional = bool(plan.final_output) and not svc.is_ready(steps)
    if plan.status == PLAN_ACTIVE and plan.final_output:
        plan.status = PLAN_CONSOLIDATING
    if complete and plan.final_output and not plan.final_provisional:
        plan.status = PLAN_DONE
    await session.commit()
    return plan


def topic_note_content(plan: StepPlan, text: str, *, provisional: bool = False) -> str:
    label = " (provisional — not every step was validated)" if provisional else ""
    return f"**From step-by-step plan: [{plan.title}](/steps/{plan.id}){label}**\n\n{text.strip()}"


async def send_to_topic(
    session: AsyncSession, plan_id: int, text: str | None, *, force: bool = False
) -> tuple[StepPlan, MessageRead]:
    """File the final output into the plan's topic as a user message, like a note.

    A provisional output is only filed on an explicit ``force``, labelled as
    such, and doesn't complete the plan.
    """
    plan = await svc.get_plan(session, plan_id)
    body = (text if text is not None else plan.final_output) or ""
    provisional = plan.final_provisional or not svc.is_ready(await svc.plan_steps(session, plan.id))
    if provisional and not force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Not every step is validated: this output is provisional. Validate the "
            "remaining steps, or send it anyway.",
        )
    if not body.strip():
        raise HTTPException(status.HTTP_409_CONFLICT, "There's no final output to send yet.")
    if plan.source_topic_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This plan isn't linked to a topic any more.")
    topic_id = plan.source_topic_id
    async with SessionLocal() as ws:
        msg = Message(
            topic_id=topic_id,
            role=MessageRole.USER,
            content=topic_note_content(plan, body, provisional=provisional),
        )
        ws.add(msg)
        await ws.commit()
        await ws.refresh(msg, attribute_names=["attachments"])
        read = MessageRead.model_validate(msg, from_attributes=True)
    if not provisional:
        plan.status = PLAN_DONE
    await session.commit()
    await publish_message_changed(topic_id)
    return plan, read


__all__ = [
    "PLAN_CONSOLIDATING",
    "ArtifactDraft",
    "FinalDraft",
    "apply_chat_reply",
    "assess_plan",
    "assess_step",
    "extract_json",
    "generate_artifact",
    "generate_final",
    "parse_steps",
    "review_plan",
    "save_final",
    "send_to_topic",
    "suggest_steps",
    "trace_in_chat",
    "use_message_as_artifact",
    "use_message_as_final",
]
