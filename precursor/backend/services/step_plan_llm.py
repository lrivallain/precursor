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

import json
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import Chat, Message, MessageRole, Step, StepPlan
from precursor.backend.models.step_plan import (
    AUTHOR_MODEL,
    PLAN_ACTIVE,
    PLAN_CONSOLIDATING,
    PLAN_DONE,
    PLAN_DRAFTING,
    STEP_TO_VALIDATE,
    STEP_VALIDATED,
)
from precursor.backend.schemas.conversation_llm import ConversationLLMSelection
from precursor.backend.schemas.message import MessageRead
from precursor.backend.schemas.step_plan import StepCreate
from precursor.backend.services import step_plans as svc
from precursor.backend.services.compaction import render_transcript
from precursor.backend.services.conversation_turn import load_model_history
from precursor.backend.services.events import publish_message_changed
from precursor.backend.services.llm.one_shot import complete_once
from precursor.backend.services.turn import run_chat_turn

from sqlalchemy import select  # isort: skip

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
    return await svc.write_artifact(
        session, step_id, text, author=AUTHOR_MODEL, mark=STEP_TO_VALIDATE
    )


async def review_plan(session: AsyncSession, plan_id: int) -> str:
    plan = await svc.get_plan(session, plan_id)
    steps = await svc.plan_steps(session, plan.id)
    artifacts = svc.render_artifacts(steps, include_unvalidated=True)
    user = f"{svc.plan_header(plan)}\n\nPlan outline:\n{svc.render_outline(steps)}"
    if artifacts:
        user += f"\n\nSteps' artifacts:\n<<<ARTIFACTS\n{artifacts}\nARTIFACTS>>>"
    return await _complete(session, plan, system=_REVIEW_SYSTEM, user=user)


# --- consolidation ------------------------------------------------------------


async def generate_final(
    session: AsyncSession,
    plan_id: int,
    *,
    instructions: str | None = None,
    force: bool = False,
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
    pending = [s for s in leaves if s.status != STEP_VALIDATED]
    if pending and not force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{len(pending)} step(s) aren't validated yet. Validate or cancel them, "
            "or consolidate anyway (the result stays provisional).",
        )
    if not any(s.artifact.strip() for s in leaves if force or s.status == STEP_VALIDATED):
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
    return FinalDraft(await save_final(session, plan_id, deliverable), "published")


async def use_message_as_final(session: AsyncSession, plan_id: int, message_id: int) -> StepPlan:
    chat_id = await _chat_id(session, plan_id=plan_id)
    content = await _chat_message(session, chat_id, message_id)
    return await save_final(session, plan_id, svc.extract_deliverable(content) or content.strip())


async def save_final(session: AsyncSession, plan_id: int, content: str) -> StepPlan:
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
    "extract_json",
    "generate_artifact",
    "generate_final",
    "parse_steps",
    "review_plan",
    "save_final",
    "send_to_topic",
    "suggest_steps",
    "use_message_as_artifact",
    "use_message_as_final",
]
