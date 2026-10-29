"""Step-by-step plans: the tree, status rules, artifacts and chats.

The model-driven actions (proposing steps, drafting artifacts, consolidating)
live in ``step_plan_llm.py``; this module is everything deterministic.

Status rules, in one place:

* A step with accepted children is a *group*: it has no work of its own and its
  ``effective_status`` rolls its children up. Only cancelling (or reopening) a
  group is allowed, and it cascades to the children.
* A step can only be validated once it has an artifact.
* Editing a validated artifact, or reopening a validated step, puts it back to
  ``to_validate`` / the requested status and marks an already-built final
  output stale.
* Writing an artifact or opening the step's chat moves an ``open`` step to
  ``in_progress``.

SQLite doesn't enforce foreign keys, so deleting a step removes its whole
subtree (and their chats) explicitly.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import (
    Chat,
    Message,
    MessageRole,
    Step,
    StepArtifactVersion,
    StepPlan,
    Topic,
    TopicSummary,
)
from precursor.backend.models.step_plan import (
    AUTHOR_MODEL,
    AUTHOR_USER,
    AUTO_RUNNING,
    PLAN_ACTIVE,
    PLAN_CONSOLIDATING,
    PLAN_DONE,
    PLAN_DRAFTING,
    STEP_CANCELLED,
    STEP_IN_PROGRESS,
    STEP_OPEN,
    STEP_PROPOSED,
    STEP_TO_VALIDATE,
    STEP_VALIDATED,
)
from precursor.backend.schemas.step_plan import (
    ActivityNote,
    Assessment,
    StepArtifactVersionRead,
    StepCreate,
    StepPlanCreate,
    StepPlanItem,
    StepPlanRead,
    StepPlanUpdate,
    StepRead,
    StepUpdate,
)
from precursor.backend.services import roles as roles_service
from precursor.backend.services.conversation_llm import validate_selection
from precursor.backend.services.events import publish_message_changed
from precursor.backend.services.slugs import allocate_unique_slug

# Plans whose automated run is alive in this process. ``auto_state`` in the
# database survives a restart, this set doesn't: that is how an interrupted run
# reads as paused instead of running forever.
RUNNING: set[int] = set()
# The step the automated run is drafting right now, per plan.
CURRENT_STEP: dict[int, int] = {}

MAX_STEPS_PER_PLAN = 200
MAX_DEPTH = 6

# A reply is a discussion; only what sits between these lines is a deliverable.
# HTML comments, so they stay invisible when the transcript renders Markdown.
ARTIFACT_OPEN = "<!-- artifact -->"
ARTIFACT_CLOSE = "<!-- /artifact -->"
_DELIVERABLE_RES = (
    re.compile(r"<!--\s*artifact\s*-->(.*?)<!--\s*/artifact\s*-->", re.DOTALL | re.IGNORECASE),
    re.compile(r"<artifact>(.*?)</artifact>", re.DOTALL | re.IGNORECASE),
)
_FENCED = re.compile(r"^```[a-zA-Z]*\n(.*)\n```$", re.DOTALL)


def extract_deliverable(reply: str) -> str | None:
    """The last marked deliverable of a model reply, or None when it has none."""
    best: tuple[int, str] | None = None
    for pattern in _DELIVERABLE_RES:
        for match in pattern.finditer(reply):
            if best is None or match.start() >= best[0]:
                best = (match.start(), match.group(1))
    if best is None:
        return None
    text = best[1].strip()
    fenced = _FENCED.match(text)
    text = fenced.group(1).strip() if fenced else text
    return text or None


_TITLE_MAX = 255

# A step's discussion starts as a placeholder; the title is the step's own.
CONSOLIDATION_CHAT_TITLE = "Consolidation"


# --- loading ------------------------------------------------------------------


async def get_plan(session: AsyncSession, plan_id: int) -> StepPlan:
    plan = await session.get(StepPlan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    return plan


async def get_step(session: AsyncSession, step_id: int) -> Step:
    step = await session.get(Step, step_id)
    if step is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Step not found")
    return step


async def plan_steps(session: AsyncSession, plan_id: int) -> list[Step]:
    rows = await session.execute(
        select(Step).where(Step.plan_id == plan_id).order_by(Step.position, Step.id)
    )
    return list(rows.scalars().all())


def children_by_parent(steps: Iterable[Step]) -> dict[int | None, list[Step]]:
    out: dict[int | None, list[Step]] = defaultdict(list)
    for s in steps:
        out[s.parent_step_id].append(s)
    for siblings in out.values():
        siblings.sort(key=lambda s: (s.position, s.id))
    return out


def ordered(steps: list[Step]) -> list[tuple[Step, int]]:
    """Depth-first, sibling-ordered ``(step, depth)`` pairs."""
    kids = children_by_parent(steps)
    out: list[tuple[Step, int]] = []

    def walk(parent: int | None, depth: int) -> None:
        for s in kids.get(parent, []):
            out.append((s, depth))
            walk(s.id, depth + 1)

    walk(None, 0)
    return out


def _accepted_children(kids: dict[int | None, list[Step]], step_id: int) -> list[Step]:
    return [c for c in kids.get(step_id, []) if c.status != STEP_PROPOSED]


def effective_statuses(steps: list[Step]) -> dict[int, str]:
    """Each step's own status for a leaf, the roll-up of its children for a group."""
    kids = children_by_parent(steps)
    memo: dict[int, str] = {}

    def effective(step: Step) -> str:
        if step.id in memo:
            return memo[step.id]
        children = _accepted_children(kids, step.id)
        if not children or step.status == STEP_PROPOSED:
            result = step.status
        else:
            child_states = [effective(c) for c in children]
            live = [s for s in child_states if s != STEP_CANCELLED]
            if not live:
                result = STEP_CANCELLED
            elif all(s == STEP_VALIDATED for s in live):
                result = STEP_VALIDATED
            elif all(s in (STEP_VALIDATED, STEP_TO_VALIDATE) for s in live):
                result = STEP_TO_VALIDATE
            elif all(s == STEP_OPEN for s in live):
                result = STEP_OPEN
            else:
                result = STEP_IN_PROGRESS
        memo[step.id] = result
        return result

    for s in steps:
        effective(s)
    return memo


def working_leaves(steps: list[Step]) -> list[Step]:
    """Leaves that count toward the plan: not cancelled, not merely suggested."""
    kids = children_by_parent(steps)
    out: list[Step] = []
    for s, _ in ordered(steps):
        if s.status in (STEP_PROPOSED, STEP_CANCELLED):
            continue
        if _accepted_children(kids, s.id):
            continue
        out.append(s)
    return out


def descendants(steps: list[Step], step_id: int) -> list[Step]:
    kids = children_by_parent(steps)
    out: list[Step] = []
    stack = list(kids.get(step_id, []))
    while stack:
        s = stack.pop()
        out.append(s)
        stack.extend(kids.get(s.id, []))
    return out


def _depth(steps: list[Step], step_id: int | None) -> int:
    by_id = {s.id: s for s in steps}
    depth = 0
    while step_id is not None and step_id in by_id:
        depth += 1
        step_id = by_id[step_id].parent_step_id
    return depth


def is_approved(step: Step) -> bool:
    """Validated by the user and still valid for the current ask and instruction."""
    return step.status == STEP_VALIDATED and not step.needs_review


def is_ready(steps: list[Step]) -> bool:
    leaves = working_leaves(steps)
    return bool(leaves) and all(is_approved(s) for s in leaves)


# --- assessments --------------------------------------------------------------
#
# An assessment is tied to a signature of what it looked at; the signature is
# recomputed on read, so a changed artifact, instruction or ask marks it stale
# without any bookkeeping on the write paths.


def _sig(*parts: str) -> str:
    digest = hashlib.sha1("\x00".join(parts).encode("utf-8"), usedforsecurity=False)
    return digest.hexdigest()


def step_signature(plan: StepPlan, step: Step) -> str:
    return _sig(plan.source_text, step.instruction, step.artifact)


def plan_signature(plan: StepPlan, steps: list[Step]) -> str:
    leaves = [f"{s.id}:{step_signature(plan, s)}" for s in working_leaves(steps)]
    return _sig(plan.source_text, plan.output_instructions or "", *leaves)


def parse_assessment(raw: str | None) -> Assessment | None:
    if not raw:
        return None
    try:
        return Assessment.model_validate(json.loads(raw))
    except ValueError:
        return None


# --- read models --------------------------------------------------------------


async def _chat_links(
    session: AsyncSession, *, step_ids: list[int], plan_id: int
) -> tuple[dict[int, tuple[int, str]], tuple[int, str] | None]:
    step_chats: dict[int, tuple[int, str]] = {}
    if step_ids:
        rows = await session.execute(
            select(Chat.step_id, Chat.id, Chat.slug)
            .where(Chat.step_id.in_(step_ids))
            .order_by(Chat.id.desc())
        )
        for sid, cid, slug in rows.all():
            if sid is not None:
                step_chats[sid] = (cid, slug)  # the oldest wins (last write)
    row = (
        await session.execute(
            select(Chat.id, Chat.slug)
            .where(Chat.step_plan_id == plan_id)
            .order_by(Chat.id)
            .limit(1)
        )
    ).first()
    return step_chats, (row[0], row[1]) if row else None


_STEP_FIELDS = tuple(
    f
    for f in StepRead.model_fields
    if f
    not in {
        "effective_status",
        "chat_id",
        "chat_slug",
        "assessment",
        "assessment_stale",
    }
)


def _step_read(
    step: Step, effective: str, chat: tuple[int, str] | None, plan: StepPlan
) -> StepRead:
    read = StepRead.model_validate(
        {**{f: getattr(step, f) for f in _STEP_FIELDS}, "effective_status": effective}
    )
    if chat is not None:
        read.chat_id, read.chat_slug = chat
    read.assessment = parse_assessment(step.assessment_json)
    read.assessment_stale = read.assessment is not None and step.assessed_sig != step_signature(
        plan, step
    )
    return read


async def to_plan_read(session: AsyncSession, plan: StepPlan) -> StepPlanRead:
    steps = await plan_steps(session, plan.id)
    effective = effective_statuses(steps)
    step_chats, consolidation = await _chat_links(
        session, step_ids=[s.id for s in steps], plan_id=plan.id
    )
    topic_title = None
    if plan.source_topic_id is not None:
        topic = await session.get(Topic, plan.source_topic_id)
        topic_title = topic.title if topic else None
    read = StepPlanRead.model_validate(plan)
    read.source_topic_title = topic_title
    read.ready_to_consolidate = is_ready(steps)
    if consolidation is not None:
        read.consolidation_chat_id, read.consolidation_chat_slug = consolidation
    read.steps = [
        _step_read(s, effective[s.id], step_chats.get(s.id), plan) for s, _ in ordered(steps)
    ]
    read.assessment = parse_assessment(plan.assessment_json)
    read.assessment_stale = read.assessment is not None and plan.assessed_sig != plan_signature(
        plan, steps
    )
    if plan.auto_state == AUTO_RUNNING and plan.id not in RUNNING:
        read.auto_state = "paused"
    read.auto_step_id = CURRENT_STEP.get(plan.id) if plan.id in RUNNING else None
    read.activity = await _recent_activity(session, plan.id, [s.id for s in steps])
    return read


ACTIVITY_LIMIT = 30


async def _recent_activity(
    session: AsyncSession, plan_id: int, step_ids: list[int]
) -> list[ActivityNote]:
    """The latest traces left in the plan's chats: what the model and the run did."""
    rows = await session.execute(
        select(Message.id, Message.content, Message.created_at, Chat.step_id)
        .join(Chat, Chat.id == Message.chat_id)
        .where(
            Message.kind == STEP_PLAN_TRACE_KIND,
            or_(Chat.step_plan_id == plan_id, Chat.step_id.in_(step_ids)),
        )
        .order_by(Message.id.desc())
        .limit(ACTIVITY_LIMIT)
    )
    return [
        ActivityNote(id=i, text=text, step_id=step_id, created_at=at)
        for i, text, at, step_id in reversed(rows.all())
    ]


async def read_step(session: AsyncSession, step: Step) -> StepRead:
    steps = await plan_steps(session, step.plan_id)
    effective = effective_statuses(steps)
    chats, _ = await _chat_links(session, step_ids=[step.id], plan_id=step.plan_id)
    plan = await get_plan(session, step.plan_id)
    return _step_read(step, effective[step.id], chats.get(step.id), plan)


async def list_plans(session: AsyncSession, topic_id: int | None = None) -> list[StepPlanItem]:
    stmt = select(StepPlan).order_by(StepPlan.created_at.desc(), StepPlan.id.desc())
    if topic_id is not None:
        stmt = stmt.where(StepPlan.source_topic_id == topic_id)
    plans = list((await session.execute(stmt)).scalars().all())
    if not plans:
        return []
    by_plan: dict[int, list[Step]] = defaultdict(list)
    rows = await session.execute(select(Step).where(Step.plan_id.in_([p.id for p in plans])))
    for s in rows.scalars().all():
        by_plan[s.plan_id].append(s)
    topic_ids = {p.source_topic_id for p in plans if p.source_topic_id is not None}
    titles: dict[int, str] = {}
    if topic_ids:
        trows = await session.execute(select(Topic.id, Topic.title).where(Topic.id.in_(topic_ids)))
        titles = {tid: title for tid, title in trows.all()}
    items: list[StepPlanItem] = []
    for p in plans:
        leaves = working_leaves(by_plan[p.id])
        items.append(
            StepPlanItem(
                id=p.id,
                title=p.title,
                status=p.status,
                source_topic_id=p.source_topic_id,
                source_topic_title=titles.get(p.source_topic_id) if p.source_topic_id else None,
                step_count=len(leaves),
                validated_count=sum(1 for s in leaves if is_approved(s)),
                final_stale=p.final_stale,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
        )
    return items


# --- plans --------------------------------------------------------------------


_STEPS_COMMAND = re.compile(r"^\s*/steps\b[ \t]*", re.IGNORECASE)


def _strip_steps_command(text: str) -> str:
    """Drop a leading ``/steps`` — the command that spawned the plan isn't the ask."""
    return _STEPS_COMMAND.sub("", text, count=1).strip()


def _title_from(text: str) -> str:
    text = _strip_steps_command(text)
    for line in text.splitlines():
        line = line.strip().lstrip("#>*- ").strip()
        if line:
            return line[:80].rstrip()
    return "Step by step"


async def _source_for(session: AsyncSession, payload: StepPlanCreate) -> tuple[str, Topic | None]:
    topic = None
    if payload.topic_id is not None:
        topic = await session.get(Topic, payload.topic_id)
        if topic is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    text = _strip_steps_command(payload.source_text or "")
    if not text and topic is not None:
        if payload.message_id is not None:
            msg = await session.get(Message, payload.message_id)
            if msg is None or msg.topic_id != topic.id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
            text = msg.content.strip()
        else:
            rows = await session.execute(
                select(Message.content)
                .where(
                    Message.topic_id == topic.id,
                    Message.role == MessageRole.USER,
                    Message.kind.is_(None),
                )
                .order_by(Message.id.desc())
                .limit(20)
            )
            # A bare "/steps" turn carries no ask of its own: fall back to the
            # latest message that does.
            text = next(
                (t for c in rows.scalars() if (t := _strip_steps_command(c or ""))),
                "",
            )
    if not text:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "There's nothing to break down yet — paste the text to work through.",
        )
    return text, topic


async def create_plan(session: AsyncSession, payload: StepPlanCreate) -> StepPlan:
    text, topic = await _source_for(session, payload)
    selection = payload.llm_selection
    await validate_selection(session, selection)
    role = (
        await roles_service.require_role(session, payload.role)
        if payload.role is not None
        else (topic.role if topic else None)
    )
    plan = StepPlan(
        title=(payload.title or "").strip() or _title_from(text),
        source_text=text,
        status=PLAN_DRAFTING,
        automation_mode=payload.automation_mode,
        source_topic_id=topic.id if topic else None,
        role=role,
        llm_selection=(
            selection.model_dump_json()
            if selection
            else (topic.llm_selection if topic is not None else None)
        ),
    )
    session.add(plan)
    await session.commit()
    if topic is not None:
        await _trace_in_topic(session, plan, topic.id)
    return plan


STEP_PLAN_TRACE_KIND = "step_plan"


async def _trace_in_topic(session: AsyncSession, plan: StepPlan, topic_id: int) -> None:
    """Leave a UI-only note in the topic so the spawned plan stays traceable."""
    session.add(
        Message(
            topic_id=topic_id,
            role=MessageRole.SYSTEM,
            kind=STEP_PLAN_TRACE_KIND,
            content=f"Started step by step: [{plan.title}](/steps/{plan.id})",
        )
    )
    await session.commit()
    await publish_message_changed(topic_id)


async def update_plan(session: AsyncSession, plan_id: int, payload: StepPlanUpdate) -> StepPlan:
    plan = await get_plan(session, plan_id)
    fields = payload.model_fields_set
    if payload.title is not None:
        plan.title = payload.title.strip()
    if payload.source_text is not None:
        if not payload.source_text.strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The ask can't be empty.")
        new_text = payload.source_text.strip()
        if new_text != plan.source_text:
            plan.source_text = new_text
            await _flag_for_review(session, plan)
    if payload.automation_mode is not None:
        plan.automation_mode = payload.automation_mode
    if "role" in fields:
        plan.role = await roles_service.require_role(session, payload.role)
    if "llm_selection" in fields:
        await validate_selection(session, payload.llm_selection)
        plan.llm_selection = (
            payload.llm_selection.model_dump_json() if payload.llm_selection else None
        )
    if "output_instructions" in fields:
        text = (payload.output_instructions or "").strip()
        plan.output_instructions = text or None
    await session.commit()
    return plan


async def delete_plan(session: AsyncSession, plan_id: int) -> None:
    plan = await get_plan(session, plan_id)
    steps = await plan_steps(session, plan_id)
    chats = (
        (
            await session.execute(
                select(Chat).where(
                    (Chat.step_plan_id == plan_id) | Chat.step_id.in_([s.id for s in steps])
                )
            )
        )
        .scalars()
        .all()
    )
    for chat in chats:
        await session.delete(chat)
    for s in steps:
        await session.execute(
            delete(StepArtifactVersion).where(StepArtifactVersion.step_id == s.id)
        )
        await session.delete(s)
    await session.delete(plan)
    await session.commit()


async def detach_topic(session: AsyncSession, topic_id: int) -> list[int]:
    """Unlink the plans started from a topic that is about to be deleted.

    SQLite won't apply ``ON DELETE SET NULL``; a dangling id could later point at
    an unrelated topic. Returns the affected plan ids so callers can notify.
    """
    rows = await session.execute(select(StepPlan).where(StepPlan.source_topic_id == topic_id))
    plans = list(rows.scalars().all())
    for plan in plans:
        plan.source_topic_id = None
    return [p.id for p in plans]


async def _flag_for_review(session: AsyncSession, plan: StepPlan) -> None:
    """The ask changed: every approval and the final output must be looked at again."""
    for step in working_leaves(await plan_steps(session, plan.id)):
        if step.status == STEP_VALIDATED:
            step.needs_review = True
    _reopen_plan(plan)


async def complete_plan(session: AsyncSession, plan_id: int) -> StepPlan:
    """Mark the plan done: it needs a final output built from approved steps only."""
    plan = await get_plan(session, plan_id)
    if plan.status == PLAN_DONE:
        return plan
    if plan.status not in (PLAN_ACTIVE, PLAN_CONSOLIDATING):
        raise HTTPException(status.HTTP_409_CONFLICT, "Start the plan first.")
    if not (plan.final_output or "").strip():
        raise HTTPException(status.HTTP_409_CONFLICT, "There's no final output yet.")
    if plan.final_stale:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The steps changed since the final output was built: regenerate it first.",
        )
    if plan.final_provisional or not is_ready(await plan_steps(session, plan_id)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Not every step is validated: this output is provisional and can't complete the plan.",
        )
    plan.status = PLAN_DONE
    await session.commit()
    return plan


def _reopen_plan(plan: StepPlan) -> None:
    """A change to the working set invalidates a built or filed final output."""
    if plan.status in (PLAN_CONSOLIDATING, PLAN_DONE):
        plan.status = PLAN_ACTIVE
    if plan.final_output:
        plan.final_stale = True


# --- steps --------------------------------------------------------------------


def _renumber(siblings: list[Step]) -> None:
    for i, s in enumerate(siblings):
        s.position = i


async def create_step(
    session: AsyncSession,
    plan: StepPlan,
    payload: StepCreate,
    *,
    author: str = AUTHOR_USER,
    proposed: bool = False,
    commit: bool = True,
) -> Step:
    steps = await plan_steps(session, plan.id)
    if len(steps) >= MAX_STEPS_PER_PLAN:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A plan holds at most {MAX_STEPS_PER_PLAN} steps."
        )
    parent_id = payload.parent_step_id
    if parent_id is not None:
        parent = next((s for s in steps if s.id == parent_id), None)
        if parent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent step not found")
        if parent.artifact.strip():
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This step already has an artifact. Split it by moving its work into "
                "sub-steps, or add a sibling instead.",
            )
        if _depth(steps, parent_id) >= MAX_DEPTH:
            raise HTTPException(status.HTTP_409_CONFLICT, "Steps can't nest that deep.")
    step = Step(
        plan_id=plan.id,
        parent_step_id=parent_id,
        position=0,
        title=payload.title.strip()[:_TITLE_MAX],
        instruction=payload.instruction.strip(),
        status=STEP_PROPOSED if proposed else STEP_OPEN,
        created_by=author,
    )
    session.add(step)
    await session.flush()
    siblings = [s for s in steps if s.parent_step_id == parent_id]
    siblings.sort(key=lambda s: (s.position, s.id))
    at = len(siblings) if payload.position is None else min(payload.position, len(siblings))
    siblings.insert(at, step)
    _renumber(siblings)
    if not proposed and plan.status != PLAN_DRAFTING:
        _reopen_plan(plan)
    if commit:
        await session.commit()
    return step


async def update_step(session: AsyncSession, step_id: int, payload: StepUpdate) -> Step:
    step = await get_step(session, step_id)
    if payload.title is not None:
        step.title = payload.title.strip()[:_TITLE_MAX]
    if payload.instruction is not None and payload.instruction.strip() != step.instruction:
        step.instruction = payload.instruction.strip()
        if step.artifact.strip():
            # What was approved or filed no longer matches what the step asks for.
            if step.status == STEP_VALIDATED:
                step.needs_review = True
            _reopen_plan(await get_plan(session, step.plan_id))
    await session.commit()
    return step


async def move_step(
    session: AsyncSession, step_id: int, parent_step_id: int | None, position: int
) -> Step:
    step = await get_step(session, step_id)
    steps = await plan_steps(session, step.plan_id)
    if parent_step_id is not None:
        parent = next((s for s in steps if s.id == parent_step_id), None)
        if parent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent step not found")
        if parent.id == step.id or parent.id in {d.id for d in descendants(steps, step.id)}:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "A step can't be moved into its own sub-steps."
            )
        if parent.artifact.strip() and parent.id != step.parent_step_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "That step already has an artifact; it can't become a group.",
            )
        subtree_depth = max(
            (_depth(steps, d.id) - _depth(steps, step.id) for d in descendants(steps, step.id)),
            default=0,
        )
        if _depth(steps, parent_step_id) + 1 + subtree_depth > MAX_DEPTH:
            raise HTTPException(status.HTTP_409_CONFLICT, "Steps can't nest that deep.")
    old_parent = step.parent_step_id
    siblings = [s for s in steps if s.parent_step_id == parent_step_id and s.id != step.id]
    siblings.sort(key=lambda s: (s.position, s.id))
    step.parent_step_id = parent_step_id
    siblings.insert(min(position, len(siblings)), step)
    _renumber(siblings)
    if old_parent != parent_step_id:
        old = [s for s in steps if s.parent_step_id == old_parent and s.id != step.id]
        old.sort(key=lambda s: (s.position, s.id))
        _renumber(old)
    await session.commit()
    return step


async def delete_step(session: AsyncSession, step_id: int) -> StepPlan:
    step = await get_step(session, step_id)
    plan = await get_plan(session, step.plan_id)
    steps = await plan_steps(session, plan.id)
    doomed = [step, *descendants(steps, step.id)]
    ids = [s.id for s in doomed]
    chats = (await session.execute(select(Chat).where(Chat.step_id.in_(ids)))).scalars().all()
    for chat in chats:
        await session.delete(chat)
    await session.execute(delete(StepArtifactVersion).where(StepArtifactVersion.step_id.in_(ids)))
    had_work = any(s.status == STEP_VALIDATED or s.artifact.strip() for s in doomed)
    parent_id = step.parent_step_id
    for s in doomed:
        await session.delete(s)
    await session.flush()
    remaining = [s for s in steps if s.id not in set(ids) and s.parent_step_id == parent_id]
    remaining.sort(key=lambda s: (s.position, s.id))
    _renumber(remaining)
    if had_work and plan.status != PLAN_DRAFTING:
        _reopen_plan(plan)
    await session.commit()
    return plan


async def accept_step(session: AsyncSession, step_id: int) -> Step:
    """Turn a suggested step (and what it hangs from or contains) into real work."""
    step = await get_step(session, step_id)
    plan = await get_plan(session, step.plan_id)
    steps = await plan_steps(session, plan.id)
    by_id = {s.id: s for s in steps}
    chain = [step]
    parent = by_id.get(step.parent_step_id) if step.parent_step_id else None
    while parent is not None:
        chain.append(parent)
        parent = by_id.get(parent.parent_step_id) if parent.parent_step_id else None
    for s in [*chain, *descendants(steps, step.id)]:
        if s.status == STEP_PROPOSED:
            s.status = STEP_OPEN
            s.created_by = AUTHOR_MODEL
    if plan.status != PLAN_DRAFTING:
        _reopen_plan(plan)
    await session.commit()
    return step


async def accept_all_proposed(session: AsyncSession, plan: StepPlan) -> int:
    steps = await plan_steps(session, plan.id)
    n = 0
    for s in steps:
        if s.status == STEP_PROPOSED:
            s.status = STEP_OPEN
            n += 1
    if n and plan.status != PLAN_DRAFTING:
        _reopen_plan(plan)
    return n


async def drop_proposed(
    session: AsyncSession, plan: StepPlan, *, parent_id: int | None = None
) -> None:
    """Delete suggestions that were never accepted (all, or one step's children)."""
    steps = await plan_steps(session, plan.id)
    doomed = [s for s in steps if s.status == STEP_PROPOSED]
    if parent_id is not None:
        subtree = {d.id for d in descendants(steps, parent_id)}
        doomed = [s for s in doomed if s.id in subtree]
    for s in doomed:
        await session.delete(s)
    await session.flush()


async def start_plan(session: AsyncSession, plan_id: int, *, accept_proposed: bool) -> StepPlan:
    """Leave the drafting screen: the plan becomes active."""
    plan = await get_plan(session, plan_id)
    if plan.status != PLAN_DRAFTING:
        raise HTTPException(status.HTTP_409_CONFLICT, "This plan has already started.")
    if accept_proposed:
        await accept_all_proposed(session, plan)
    else:
        await drop_proposed(session, plan)
    steps = await plan_steps(session, plan.id)
    if not any(s.status != STEP_PROPOSED for s in steps):
        raise HTTPException(status.HTTP_409_CONFLICT, "Add at least one step before starting.")
    plan.status = PLAN_ACTIVE
    await session.commit()
    return plan


# --- status and artifacts -----------------------------------------------------


async def set_step_status(session: AsyncSession, step_id: int, new_status: str) -> Step:
    step = await get_step(session, step_id)
    plan = await get_plan(session, step.plan_id)
    if step.status == STEP_PROPOSED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Accept this suggestion first.")
    steps = await plan_steps(session, plan.id)
    subtree = descendants(steps, step.id)
    group = any(c.status != STEP_PROPOSED for c in subtree if c.parent_step_id == step.id)

    if group:
        if new_status == STEP_CANCELLED:
            targets = [step, *subtree]
            for s in targets:
                if s.status != STEP_PROPOSED:
                    s.status = STEP_CANCELLED
        elif new_status == STEP_OPEN:
            step.status = STEP_OPEN
            for s in subtree:
                if s.status == STEP_CANCELLED:
                    s.status = STEP_OPEN
        else:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This step is split into sub-steps — work and validate those instead.",
            )
        _reopen_plan(plan)
    else:
        if new_status == STEP_VALIDATED and not step.artifact.strip():
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Write or generate an artifact before validating."
            )
        if new_status == STEP_TO_VALIDATE and not step.artifact.strip():
            raise HTTPException(status.HTTP_409_CONFLICT, "There's no artifact to validate yet.")
        previous = step.status
        step.status = new_status
        step.needs_review = False
        if previous != new_status and (
            STEP_VALIDATED in (previous, new_status) or STEP_CANCELLED in (previous, new_status)
        ):
            _reopen_plan(plan)
    await session.commit()
    return step


async def write_artifact(
    session: AsyncSession,
    step_id: int,
    content: str,
    *,
    author: str = AUTHOR_USER,
    mark: str | None = None,
) -> Step:
    """Save ``content`` as the step's artifact (a new version when it differs).

    ``mark`` forces the resulting status (the model's drafts land in
    ``to_validate``); otherwise a validated step drops back to ``to_validate``
    and an untouched one becomes ``in_progress``.
    """
    step = await get_step(session, step_id)
    plan = await get_plan(session, step.plan_id)
    if step.status == STEP_PROPOSED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Accept this suggestion first.")
    steps = await plan_steps(session, plan.id)
    if any(c.parent_step_id == step.id and c.status != STEP_PROPOSED for c in steps):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This step is split into sub-steps; it has no artifact."
        )
    text = content.strip("\n")
    changed = text != step.artifact
    if changed:
        known = (
            (
                await session.execute(
                    select(StepArtifactVersion)
                    .where(
                        StepArtifactVersion.step_id == step.id, StepArtifactVersion.content == text
                    )
                    .order_by(StepArtifactVersion.version.desc())
                )
            )
            .scalars()
            .first()
        )
        if known is not None:
            # Same text as an earlier draft: point back at it instead of
            # recording a duplicate.
            step.artifact_version = known.version
            step.artifact = text
        else:
            latest = (
                await session.execute(
                    select(func.max(StepArtifactVersion.version)).where(
                        StepArtifactVersion.step_id == step.id
                    )
                )
            ).scalar() or 0
            step.artifact_version = max(latest, step.artifact_version) + 1
            step.artifact = text
            session.add(
                StepArtifactVersion(
                    step_id=step.id, version=step.artifact_version, content=text, author=author
                )
            )
    _settle_artifact(plan, step, changed=changed, mark=mark)
    await session.commit()
    return step


def _settle_artifact(plan: StepPlan, step: Step, *, changed: bool, mark: str | None) -> None:
    """Status, review flag and plan reopening after a step's artifact moved."""
    was_validated = step.status == STEP_VALIDATED
    if mark is not None and step.status != STEP_CANCELLED:
        step.status = mark
    elif changed or step.status == STEP_OPEN:
        if step.status == STEP_VALIDATED:
            step.status = STEP_TO_VALIDATE
        elif step.status == STEP_OPEN:
            step.status = STEP_IN_PROGRESS
    if step.status != STEP_VALIDATED:
        step.needs_review = False
    if (was_validated and (changed or step.status != STEP_VALIDATED)) or (
        changed and (plan.final_output or "").strip()
    ):
        _reopen_plan(plan)


async def list_versions(session: AsyncSession, step_id: int) -> list[StepArtifactVersionRead]:
    await get_step(session, step_id)
    rows = await session.execute(
        select(StepArtifactVersion)
        .where(StepArtifactVersion.step_id == step_id)
        .order_by(StepArtifactVersion.version.desc())
    )
    return [StepArtifactVersionRead.model_validate(v) for v in rows.scalars().all()]


async def revert_artifact(session: AsyncSession, step_id: int, version: int) -> Step:
    row = (
        await session.execute(
            select(StepArtifactVersion).where(
                StepArtifactVersion.step_id == step_id, StepArtifactVersion.version == version
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")
    step = await get_step(session, step_id)
    plan = await get_plan(session, step.plan_id)
    if step.status == STEP_PROPOSED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Accept this suggestion first.")
    # Restoring points the step back at an existing version: the history keeps
    # one row per distinct draft, so nothing new is recorded.
    changed = row.content != step.artifact
    step.artifact = row.content
    step.artifact_version = row.version
    _settle_artifact(plan, step, changed=changed, mark=None)
    await session.commit()
    return step


# --- chats --------------------------------------------------------------------


# Creating a chat is check-then-insert, and the SPA asks for it from several
# places at once (a remounting panel, a draft button). Without a lock both
# callers see "none yet" and the step ends up with two discussions, one of them
# empty. The lock is taken before the session reads anything, so the second
# caller's snapshot already includes the first one's chat.
_chat_locks: dict[tuple[str, int], asyncio.Lock] = defaultdict(asyncio.Lock)


async def ensure_step_chat(session: AsyncSession, step_id: int) -> Chat:
    """The step's discussion, created on first use."""
    async with _chat_locks["step", step_id]:
        step = await get_step(session, step_id)
        if step.status == STEP_PROPOSED:
            raise HTTPException(status.HTTP_409_CONFLICT, "Accept this suggestion first.")
        existing = (
            await session.execute(
                select(Chat).where(Chat.step_id == step.id).order_by(Chat.id).limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        plan = await get_plan(session, step.plan_id)
        chat = Chat(
            title=step.title[:_TITLE_MAX],
            slug=await allocate_unique_slug(session, uuid4().hex, Chat),
            role=plan.role,
            llm_selection=plan.llm_selection,
            step_id=step.id,
        )
        session.add(chat)
        if step.status == STEP_OPEN:
            step.status = STEP_IN_PROGRESS
        await session.commit()
        return chat


async def ensure_consolidation_chat(session: AsyncSession, plan_id: int) -> Chat:
    async with _chat_locks["plan", plan_id]:
        plan = await get_plan(session, plan_id)
        existing = (
            await session.execute(
                select(Chat).where(Chat.step_plan_id == plan.id).order_by(Chat.id).limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        chat = Chat(
            title=f"{CONSOLIDATION_CHAT_TITLE}: {plan.title}"[:_TITLE_MAX],
            slug=await allocate_unique_slug(session, uuid4().hex, Chat),
            role=plan.role,
            llm_selection=plan.llm_selection,
            step_plan_id=plan.id,
        )
        session.add(chat)
        await session.commit()
        return chat


# --- prompt grounding ---------------------------------------------------------

_SOURCE_CAP = 30_000
_ARTIFACT_CAP = 20_000


def _cap(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "\n…(truncated)"


def render_outline(steps: list[Step], *, mark: int | None = None) -> str:
    effective = effective_statuses(steps)
    lines: list[str] = []
    for s, depth in ordered(steps):
        if s.status == STEP_PROPOSED:
            continue
        pointer = "  ← this step" if s.id == mark else ""
        lines.append(f"{'  ' * depth}- {s.title} [{effective[s.id]}]{pointer}")
    return "\n".join(lines)


def render_artifacts(steps: list[Step], *, include_unvalidated: bool = False) -> str:
    """Markdown of the steps' results in plan order, groups as headings."""
    kids = children_by_parent(steps)
    blocks: list[str] = []
    for s, depth in ordered(steps):
        if s.status in (STEP_PROPOSED, STEP_CANCELLED):
            continue
        heading = "#" * min(depth + 2, 6)
        if _accepted_children(kids, s.id):
            blocks.append(f"{heading} {s.title}")
            continue
        if not is_approved(s) and not (include_unvalidated and s.artifact.strip()):
            continue
        note = "" if is_approved(s) else " (not validated yet)"
        blocks.append(f"{heading} {s.title}{note}\n\n{_cap(s.artifact, _ARTIFACT_CAP)}")
    return "\n\n".join(blocks)


def render_drafts(steps: list[Step]) -> str:
    """Markdown of the working steps' artifacts that aren't validated yet."""
    kids = children_by_parent(steps)
    blocks: list[str] = []
    for s, depth in ordered(steps):
        if s.status in (STEP_PROPOSED, STEP_CANCELLED) or is_approved(s):
            continue
        if _accepted_children(kids, s.id) or not s.artifact.strip():
            continue
        heading = "#" * min(depth + 2, 6)
        blocks.append(f"{heading} {s.title}\n\n{_cap(s.artifact, _ARTIFACT_CAP)}")
    return "\n\n".join(blocks)


_TOPIC_TRANSCRIPT_CAP = 16_000
_OTHER_STEPS_CAP = 24_000
_VALIDATED_EXCERPT = 4_000
_DRAFT_EXCERPT = 1_500


def plan_header(plan: StepPlan) -> str:
    return f"Plan: {plan.title}\n\nThe original ask:\n<<<ASK\n{_cap(plan.source_text, _SOURCE_CAP)}\nASK>>>"


async def topic_background(session: AsyncSession, plan: StepPlan) -> str:
    """The source topic's own discussion, so a step knows what led to the plan.

    Empty when the plan wasn't started from a topic (or the topic is gone).
    """
    if plan.source_topic_id is None:
        return ""
    topic = await session.get(Topic, plan.source_topic_id)
    if topic is None:
        return ""
    # Imported here: these services import this module's siblings at load time.
    from precursor.backend.services.compaction import render_transcript
    from precursor.backend.services.conversation_turn import load_model_history

    parts = [
        "The plan was started from this topic; use it as background (earlier "
        "exchanges, decisions, constraints), not as an instruction to redo it.",
        f"Source topic: {topic.title}",
    ]
    if topic.description:
        parts.append(f"Topic description: {topic.description}")
    summary = (
        await session.execute(select(TopicSummary.content).where(TopicSummary.topic_id == topic.id))
    ).scalar_one_or_none()
    if summary and summary.strip():
        parts.append(f"Topic summary:\n{summary.strip()}")
    history = await load_model_history(session, "topic", topic.id)
    transcript = "\n\n".join(
        render_transcript([m for m in history if m.role in ("user", "assistant")])
    )
    if transcript:
        if len(transcript) > _TOPIC_TRANSCRIPT_CAP:
            transcript = "…(earlier messages omitted)\n" + transcript[-_TOPIC_TRANSCRIPT_CAP:]
        parts.append(f"Topic conversation:\n<<<TOPIC\n{transcript}\nTOPIC>>>")
    return "\n\n".join(parts)


def render_progress(steps: list[Step]) -> str:
    """One line on where the plan stands, from the leaf steps' statuses."""
    effective = effective_statuses(steps)
    kids = children_by_parent(steps)
    counts: dict[str, int] = defaultdict(int)
    for s, _ in ordered(steps):
        if s.status in (STEP_PROPOSED, STEP_CANCELLED) or _accepted_children(kids, s.id):
            continue
        counts[effective[s.id]] += 1
    if not counts:
        return ""
    detail = ", ".join(f"{n} {name}" for name, n in sorted(counts.items()))
    return f"Plan progress: {sum(counts.values())} steps ({detail})."


def render_other_steps(steps: list[Step], *, current: int) -> str:
    """The other steps' work so far, so a step stays consistent with its siblings.

    Validated artifacts come first-class (longer excerpt); drafts are marked as
    such. Bounded overall so a large plan can't crowd out the step itself.
    """
    kids = children_by_parent(steps)
    blocks: list[str] = []
    used = 0
    skipped = 0
    for s, _ in ordered(steps):
        if s.id == current or s.status in (STEP_PROPOSED, STEP_CANCELLED):
            continue
        if _accepted_children(kids, s.id) or not s.artifact.strip():
            continue
        validated = s.status == STEP_VALIDATED
        block = (
            f"### {s.title} [{s.status}]\n"
            f"{_cap(s.artifact, _VALIDATED_EXCERPT if validated else _DRAFT_EXCERPT)}"
        )
        if used + len(block) > _OTHER_STEPS_CAP:
            skipped += 1
            continue
        used += len(block)
        blocks.append(block)
    if skipped:
        blocks.append(f"…({skipped} more step(s) omitted)")
    return "\n\n".join(blocks)


async def step_grounding(
    session: AsyncSession, plan: StepPlan, steps: list[Step], step: Step
) -> list[str]:
    """The plan-wide context blocks shared by a step's chat and its drafting."""
    parts = [plan_header(plan)]
    if plan.output_instructions:
        parts.append(f"The final deliverable will be:\n{plan.output_instructions}")
    background = await topic_background(session, plan)
    if background:
        parts.append(background)
    parts.append(f"Plan outline:\n{render_outline(steps, mark=step.id)}")
    progress = render_progress(steps)
    if progress:
        parts.append(progress)
    others = render_other_steps(steps, current=step.id)
    if others:
        parts.append(
            f"What the other steps have produced so far:\n<<<OTHER_STEPS\n{others}\nOTHER_STEPS>>>"
        )
    return parts


async def build_step_context(session: AsyncSession, chat: Chat) -> str:
    """System-prompt block for a step's or a plan's consolidation chat.

    Empty for any other chat.
    """
    if chat.step_id is not None:
        step = await session.get(Step, chat.step_id)
        if step is None:
            return ""
        plan = await session.get(StepPlan, step.plan_id)
        if plan is None:
            return ""
        steps = await plan_steps(session, plan.id)
        parts = [
            "This chat works on ONE step of a larger step-by-step plan. Stay on "
            "this step: help the user think it through, answer their questions and "
            "draft its result. When the user asks you to write, redo or change the "
            f"artifact, put the WHOLE artifact between the lines `{ARTIFACT_OPEN}` "
            f"and `{ARTIFACT_CLOSE}` (nothing but the deliverable inside, never a "
            "diff or an excerpt): the app saves exactly that as a new, unvalidated "
            "version of the step's artifact right away. Don't use the markers for "
            "anything else; questions, alternatives, explanations and notes stay "
            "outside them, and a reply without markers leaves the artifact untouched."
            + (
                " This step is validated, so a marked artifact is NOT applied until "
                "the user reopens the step: say so instead of promising a change."
                if step.status == STEP_VALIDATED
                else ""
            ),
            *await step_grounding(session, plan, steps, step),
            f"Current step: {step.title}",
        ]
        if step.instruction:
            parts.append(f"What this step must produce:\n{step.instruction}")
        if step.artifact.strip():
            parts.append(
                f"Current artifact (version {step.artifact_version}, status {step.status}):\n"
                f"<<<ARTIFACT\n{_cap(step.artifact, _ARTIFACT_CAP)}\nARTIFACT>>>"
            )
        else:
            parts.append("The step has no artifact yet.")
        return "\n\n".join(parts)
    if chat.step_plan_id is not None:
        plan = await session.get(StepPlan, chat.step_plan_id)
        if plan is None:
            return ""
        steps = await plan_steps(session, plan.id)
        parts = [
            "This chat is the final consolidation of a step-by-step plan: the user "
            "reviews how the validated steps combine into one deliverable and asks "
            "for adjustments. Ground every statement in the steps' artifacts. When the "
            "user asks you to write or change the deliverable, put the WHOLE "
            f"deliverable between the lines `{ARTIFACT_OPEN}` and `{ARTIFACT_CLOSE}` "
            "(nothing but the deliverable inside, never a diff): the app saves it "
            "right away as the consolidated draft. Keep questions and notes outside "
            "the markers; a reply without markers changes nothing.",
            plan_header(plan),
        ]
        background = await topic_background(session, plan)
        if background:
            parts.append(background)
        parts.append(f"Plan outline:\n{render_outline(steps)}")
        if plan.output_instructions:
            parts.append(f"Requested output:\n{plan.output_instructions}")
        artifacts = render_artifacts(steps)
        if artifacts:
            parts.append(f"Validated artifacts:\n<<<ARTIFACTS\n{artifacts}\nARTIFACTS>>>")
        drafts = render_drafts(steps)
        if drafts:
            parts.append(
                "Drafts NOT validated yet (provisional: use them only when the user "
                f"asks to consolidate anyway):\n<<<DRAFTS\n{drafts}\nDRAFTS>>>"
            )
        if plan.final_output:
            stale = " (out of date: a step changed since)" if plan.final_stale else ""
            parts.append(
                f"Current consolidated draft{stale}:\n"
                f"<<<FINAL\n{_cap(plan.final_output, _ARTIFACT_CAP * 2)}\nFINAL>>>"
            )
        return "\n\n".join(parts)
    return ""
