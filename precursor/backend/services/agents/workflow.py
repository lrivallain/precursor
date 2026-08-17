"""Workflow coordinator — drives a :class:`Workflow` through its steps.

The workflow is the coordinator (n8n/Temporal-style): agents stay plain,
reusable units and never reference each other. Starting a workflow runs step 0's
agent; when that agent *rests* (idle / completed) the manager's completion seam
calls :func:`advance_for_run`, which starts the next step's agent — injecting
the previous step's published artifacts as a kickoff preamble — until the last
step finishes and the workflow is marked ``completed``.

Design notes
------------
* **Idle *or* completed = step done.** A plain single-turn agent rests at
  ``idle`` (not ``completed``) unless it has autonomy/deps, and workflows create
  no agent-deps. So advancement treats both resting states as "this step
  finished successfully"; only ``failed`` / ``blocked`` / ``cancelled`` divert
  the run.
* **Fresh session everywhere.** Every function takes an explicit
  ``AsyncSession`` (opened by the caller) so DB writes commit independently of
  the request/worker that triggered them.
* **Manager is injected** to avoid a circular import (manager → service happens
  lazily inside ``_advance_workflows``).
* **One advance at a time per workflow.** The completion seam fires from a
  fire-and-forget task, so several advances for the same agent can be in flight
  at once; :func:`_workflow_lock` serialises them and each re-reads the run
  state after acquiring it. See :func:`advance_for_run`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from precursor.backend.models.agent_artifact import AgentArtifact
from precursor.backend.models.agent_event import AgentEventRecord
from precursor.backend.models.agent_run import AgentRun
from precursor.backend.models.agent_session import AgentSession
from precursor.backend.models.workflow import (
    WORKFLOW_PRODUCING_KINDS,
    WORKFLOW_RUN_TRIGGERS,
    WORKFLOW_STEP_REJECT_POLICIES,
    Workflow,
    WorkflowRun,
    WorkflowRunStep,
    WorkflowStep,
)
from precursor.backend.services.events import publish_workflow_changed
from precursor.backend.services.workflow_state import (
    build_state_index_prompt,
    render_step_instructions,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from precursor.backend.services.agents.manager import AgentManager

logger = logging.getLogger(__name__)

# A step's agent reaching one of these means "this step is done, advance."
STEP_DONE_STATUSES = ("idle", "completed")
# These divert the whole run instead of advancing.
STEP_FAILED_STATUSES = ("failed",)
# The agent stopped and *raised a question*: its turn is over and a human has to
# answer before it can be re-driven, so the run parks.
STEP_BLOCKED_STATUSES = ("blocked",)
# The agent is waiting on a tool-permission decision. Deliberately NOT a blocked
# status: the turn is still alive and resumes by itself the moment the gate is
# answered. Pausing the run for it meant every single tool call in a step closed
# the trace and demanded a manual "Resume" — an agent making five calls blocked
# five times — so the run stays ``running`` and only the card is surfaced.
STEP_AWAITING_PERMISSION_STATUSES = ("needs_approval",)
STEP_CANCELLED_STATUSES = ("cancelled",)

# --- Advance serialisation --------------------------------------------------
# Advances arrive as fire-and-forget tasks from the manager's completion seam, so
# two can run concurrently for the same workflow in separate sessions. Without a
# lock both read ``current_step_id`` still pointing at the step that just
# finished, so both advance it: two trace rows, two real ``start_task`` launches
# for one logical step entry, and the step's tokens counted twice in the run
# rollup. One lock per workflow, in-process (Precursor is a single uvicorn
# process, so this is the whole coordinator).
_advance_locks: dict[int, asyncio.Lock] = {}


@contextlib.asynccontextmanager
async def _workflow_lock(workflow_id: int) -> AsyncIterator[None]:
    """Hold the advance lock for one workflow.

    Callers **must** re-read the workflow inside the lock: whoever held it before
    them has very likely just moved ``current_step_id`` on.
    """
    lock = _advance_locks.setdefault(workflow_id, asyncio.Lock())
    async with lock:
        yield


# --- Gate verdict grammar --------------------------------------------------
# A gate step's agent votes by ending its turn with
# ``OBJECTIVE_COMPLETE: PASS: …`` / ``OBJECTIVE_COMPLETE: FAIL: …`` (folded into
# ``result_summary``). Parsing is lenient — a small synonym set, FAIL wins ties,
# and a missing/garbled verdict defaults to PASS (fail-open; ``max_loops`` caps
# runaway loops) so a workflow never wedges on an unparseable check.
_VERDICT_FAIL_RE = re.compile(
    r"\b(FAIL(?:ED|URE)?|UNSAFE|REJECT(?:ED)?|NO[\s-]?GO|DENY|DENIED|NOT\s+(?:SAFE|OK|APPROVED))\b",
    re.IGNORECASE,
)
_VERDICT_PASS_RE = re.compile(
    r"\b(PASS(?:ED)?|SAFE|APPROVE[SD]?|ACCEPT(?:ED)?|OK|GO|YES)\b",
    re.IGNORECASE,
)
# Strip trailing directive lines so a forwarded body isn't polluted by control text.
_DIRECTIVE_LINE_RE = re.compile(
    r"^\s*(OBJECTIVE_COMPLETE|NEED_INPUT|PROGRESS)\s*:.*$", re.IGNORECASE | re.MULTILINE
)

_GATE_PREAMBLE = (
    "You are a QUALITY GATE in an automated workflow. Judge the material from the "
    "previous step (shown above) against your objective. Do NOT rewrite it or "
    "produce new content — only decide.\n\n"
    "End your turn with EXACTLY ONE final line, and nothing after it:\n"
    "  OBJECTIVE_COMPLETE: PASS: <one short reason>   — if it meets the objective\n"
    "  OBJECTIVE_COMPLETE: FAIL: <what must change>   — if it does not"
)

# Appended to every non-gate (task) step's kickoff. A workflow runs unattended:
# there is no human waiting to answer mid-run, so a step that emits NEED_INPUT or
# asks a clarifying question just wedges the whole pipeline (the coordinator parks
# it as ``blocked`` and pauses the run). This forces the step to commit to the
# most reasonable interpretation of its objective and actually produce the
# deliverable, rather than stalling to ask what to do.
_TASK_PREAMBLE = (
    "You are an automated step in a workflow — it runs unattended, so there is no "
    "human available to answer questions mid-run. Act fully autonomously: carry "
    "out YOUR objective directly, treating the material above as your input. Do "
    "NOT ask for clarification, present a menu of options, or request "
    "confirmation, and never emit NEED_INPUT — if a detail is underspecified, "
    "pick the most reasonable interpretation and proceed anyway. Produce the "
    "actual deliverable your objective calls for (not a description of what you "
    "could do), publish it with an ARTIFACT directive, and end with "
    "'OBJECTIVE_COMPLETE: <2-3 sentence summary>'."
)


def _parse_verdict(text: str | None) -> bool:
    """True = PASS. FAIL takes precedence; empty/ambiguous defaults to PASS."""
    if not text:
        return True
    if _VERDICT_FAIL_RE.search(text):
        return False
    if _VERDICT_PASS_RE.search(text):
        return True
    return True


def _verdict_reason(text: str | None) -> str:
    """Extract the human-facing reason from a gate verdict for loop-back context."""
    if not text:
        return "The previous attempt did not meet the objective."
    cleaned = text.strip()
    # Drop a leading OBJECTIVE_COMPLETE: control token (present when reading the raw
    # archived turn) then the PASS:/FAIL: verdict token, so the reason reads naturally.
    cleaned = re.sub(r"^\s*OBJECTIVE[_ ]COMPLETE\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"^\s*(PASS|FAIL|UNSAFE|SAFE|REJECT(?:ED)?)\s*[:\-]?\s*", "", cleaned, flags=re.IGNORECASE
    )
    return cleaned.strip()[:600] or "The previous attempt did not meet the objective."


def _fail_preamble(reason: str) -> str:
    return (
        "### A quality check rejected the previous attempt\n"
        f"Reason: {reason.strip()}\n\n"
        "Produce a new version that fixes this specific problem."
    )


def _run_input_preamble(run_input: str) -> str:
    """Frame the human's per-run brief as the run's subject.

    A workflow definition is generic and reusable ("analyse the file, review it,
    report"); the brief is what *this* run is about ("the file is /tmp/sales.csv,
    focus on Q3"). It leads the preamble — ahead of upstream output — because it
    is the run's intent, and it's given to **every** step so a reviewer or gate
    three stages down still knows what was actually asked for.
    """
    return (
        "### Run brief from the human\n"
        "This is what this particular run of the workflow is about. Treat it as the "
        "primary subject of your objective and honour any constraints it states.\n\n"
        f"{run_input.strip()}"
    )


def _step_instructions_preamble(instructions: str) -> str:
    """The step's own mandate, layered on top of the agent's standing objective.

    An agent row carries a general capability ("summarise what you're given");
    the step says how *this* stage should apply it ("three bullets, exec tone").
    Marked as taking precedence so one reusable agent can behave differently in
    each workflow that references it.
    """
    return (
        "### Your specific instructions for this step\n"
        "These refine your standing objective for this stage of the workflow and "
        "take precedence over it where they differ.\n\n"
        f"{instructions.strip()}"
    )


def _rejection_preamble(feedback: str) -> str:
    """A human rejected the work at an approval checkpoint — redo it with notes."""
    return (
        "### A human reviewed the work and sent it back\n"
        f"Their feedback: {feedback.strip()}\n\n"
        "Produce a new version that addresses this feedback directly."
    )


def _unblock_preamble(guidance: str, question: str | None = None) -> str:
    """The human's answer to whatever parked this step.

    A step blocks when its agent raises a question it can't resolve alone.
    Echoing the question back alongside the answer keeps the exchange legible in
    the trace — and stops the retry from re-asking the thing it was just told.
    """
    parts = ["### You asked a question and a human answered it"]
    if question and question.strip():
        parts.append(f"You asked: {question.strip()}")
    parts.append(f"Their answer: {guidance.strip()}")
    parts.append(
        "Treat this as settled and carry on. Do not ask it again — complete your "
        "step with this answer."
    )
    return "\n\n".join(parts)


def _approval_notes_preamble(notes: str) -> str:
    """Directives a reviewer attached when approving an earlier checkpoint.

    Distinct from the run brief (set before anything ran): these are course
    corrections a human made *mid-run*, having seen the work so far, so they
    override earlier instructions where they conflict.
    """
    return (
        "### Instructions added by the reviewer at an approval checkpoint\n"
        "A human reviewed the work mid-run and asked for this. It applies to your "
        "step and takes precedence over the original brief where they conflict.\n\n"
        f"{notes.strip()}"
    )


async def _publish(workflow: Workflow) -> None:
    """Broadcast a workflow change with the state the client needs to notify on."""
    await publish_workflow_changed(workflow.id, status=workflow.status, name=workflow.name)


async def _load_workflow(session: AsyncSession, workflow_id: int) -> Workflow | None:
    result = await session.execute(
        select(Workflow)
        .where(Workflow.id == workflow_id)
        .options(selectinload(Workflow.steps).selectinload(WorkflowStep.agent))
    )
    return result.scalar_one_or_none()


async def _last_assistant_message(
    session: AsyncSession, agent_id: int, agent_run_id: int | None = None
) -> str | None:
    """The newest persisted ``assistant_message`` body (uncapped).

    Tier-1 hand-off: an agent that ends its turn with ``OBJECTIVE_COMPLETE:`` has
    its ``result_summary`` folded to the terse directive summary, losing the full
    output. The durable event archive still holds the complete assistant message,
    so a bare generative step ("tell me a story") forwards its whole body to the
    next step even after the directive fold.

    Scope this to ``agent_run_id`` whenever the caller knows which execution it
    is asking about. The archive spans every run of the agent — that is what
    makes it the transcript a user reads — so an agent-wide read of "the newest
    message" is really "whichever concurrent run spoke last". For a gate that
    means workflow B can pick up workflow A's ``PASS`` and wave a failing
    deliverable through. The agent-wide read stays the default only for callers
    with no run in hand.
    """
    query = select(AgentEventRecord.payload).where(AgentEventRecord.agent_session_id == agent_id)
    if agent_run_id is not None:
        query = query.where(AgentEventRecord.agent_run_id == agent_run_id)
    rows = await session.execute(query.order_by(AgentEventRecord.id.desc()).limit(100))
    for payload in rows.scalars():
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if data.get("kind") == "assistant_message":
            text = (data.get("text") or "").strip()
            if text:
                return text
    return None


async def _run_scoped_artifacts(
    session: AsyncSession,
    agent_id: int,
    workflow_run_id: int | None,
) -> list[AgentArtifact]:
    """The artifacts an agent published, narrowed to one workflow run when known.

    A shared agent's ``agent_artifacts`` rows span every execution it has ever
    had. Scoping them through ``AgentRun.workflow_run_id`` keeps a workflow's
    blackboard to what *this* run produced, so a concurrent workflow driving the
    same agent can't leak its deliverables into this one's context. Falls back to
    the agent-wide read when no workflow run is in play (a manual call, or rows
    written before runs existed).
    """
    stmt = select(AgentArtifact).where(AgentArtifact.agent_id == agent_id)
    if workflow_run_id is not None:
        stmt = stmt.join(AgentRun, AgentArtifact.agent_run_id == AgentRun.id).where(
            AgentRun.workflow_run_id == workflow_run_id
        )
    result = await session.execute(stmt.order_by(AgentArtifact.created_at.asc()))
    return list(result.scalars().all())


async def collect_step_context(
    session: AsyncSession,
    prev_agent_id: int,
    *,
    workflow_run_id: int | None = None,
) -> str:
    """Format the previous step's output + artifacts as a kickoff preamble.

    Workflow-specific (no agent-deps): the next step is fed *only* the immediately
    preceding step's output, mirroring a linear pipeline hand-off. Prefers the
    full assistant message (survives ``OBJECTIVE_COMPLETE`` folding) and falls
    back to ``result_summary``. Returns an empty string when nothing was produced.
    """
    agent = await session.get(AgentSession, prev_agent_id)
    if agent is None:
        return ""
    parts: list[str] = []
    prev_run = await _current_run_for(session, prev_agent_id, workflow_run_id)
    body = await _last_assistant_message(
        session, prev_agent_id, prev_run.id if prev_run is not None else None
    )
    if body:
        body = _DIRECTIVE_LINE_RE.sub("", body).strip()
    if not body:
        summary = await _prev_step_summary(session, prev_agent_id, workflow_run_id)
        if summary:
            body = summary.strip()
    if body:
        parts.append(body)
    for art in await _run_scoped_artifacts(session, prev_agent_id, workflow_run_id):
        parts.append(f"[{art.title}]\n{art.content}".strip())
    if not parts:
        return ""
    label = agent.title or f"Step agent {prev_agent_id}"
    return (
        "You are one step in a workflow. Here is the output of the previous step. "
        "Use it as the input for your objective.\n\n"
        f"### From previous step: {label}\n" + "\n\n".join(parts)
    )


async def _prev_step_summary(
    session: AsyncSession,
    agent_id: int,
    workflow_run_id: int | None,
) -> str | None:
    """The previous step's result summary, read from its run when we can.

    The agent's own ``result_summary`` is a mirror of whatever ran last on it,
    which for a shared agent may be another workflow's turn entirely.
    """
    if workflow_run_id is not None:
        result = await session.execute(
            select(AgentRun.result_summary)
            .where(
                AgentRun.agent_id == agent_id,
                AgentRun.workflow_run_id == workflow_run_id,
            )
            .order_by(AgentRun.id.desc())
            .limit(1)
        )
        summary = result.scalar_one_or_none()
        if summary:
            return summary
    agent = await session.get(AgentSession, agent_id)
    return agent.result_summary if agent else None


async def _last_run_summary(
    session: AsyncSession,
    agent_id: int,
    workflow_run_id: int | None,
) -> str | None:
    """This agent's newest result summary within one workflow run."""
    return await _prev_step_summary(session, agent_id, workflow_run_id)


async def collect_prior_artifacts(
    session: AsyncSession,
    agent_ids: list[int],
    *,
    workflow_run_id: int | None = None,
) -> str:
    """Format artifacts published by *earlier* steps as a shared-board digest.

    A workflow accumulates a **blackboard**: beyond the immediate hand-off, each
    step can also read the durable artifacts every step before it published (a
    research inventory the drafter *and* the reviewer both need, say). We list
    each earlier producer's artifacts, oldest step first, labelled by step, and
    skip steps that published nothing so the preamble stays tight. The immediate
    previous step is handled by ``collect_step_context``; pass only the steps
    *before* it here so nothing is duplicated.
    """
    if not agent_ids:
        return ""
    sections: list[str] = []
    for aid in agent_ids:
        items = await _run_scoped_artifacts(session, aid, workflow_run_id)
        if not items:
            continue
        agent = await session.get(AgentSession, aid)
        label = agent.title if agent and agent.title else f"Step agent {aid}"
        body = "\n\n".join(f"[{a.title}]\n{a.content}".strip() for a in items)
        sections.append(f"#### {label}\n{body}")
    if not sections:
        return ""
    return (
        "### Artifacts from earlier steps in this workflow\n"
        "These durable outputs were published by steps before the previous one. "
        "Use them as reference material for your objective.\n\n" + "\n\n".join(sections)
    )


async def _clear_agent_artifacts(session: AsyncSession, agent_id: int) -> None:
    arts = await session.execute(select(AgentArtifact).where(AgentArtifact.agent_id == agent_id))
    for art in arts.scalars().all():
        await session.delete(art)


async def _clear_run_artifacts(session: AsyncSession, agent_run_id: int) -> None:
    arts = await session.execute(
        select(AgentArtifact).where(AgentArtifact.agent_run_id == agent_run_id)
    )
    for art in arts.scalars().all():
        await session.delete(art)


async def _tidy_gate_result(
    session: AsyncSession,
    agent: AgentSession | None,
    agent_run: AgentRun | None,
    verdict_text: str,
    passed: bool,
) -> None:
    """Keep a gate's stored output clean — a gate is a judge, not a producer.

    A gate ends its turn with ``OBJECTIVE_COMPLETE: PASS/FAIL: <reason>``, which the
    manager folds verbatim into ``result_summary`` and auto-captures as a "Result"
    artifact. Left as-is that leaks the raw ``PASS:``/``FAIL:`` directive token into
    the step's displayed result and drops a non-deliverable verdict onto the shared
    blackboard. We rewrite the summary to a plain human phrasing and delete the
    gate run's artifacts so downstream steps only ever inherit real content.
    """
    if agent is None and agent_run is None:
        return
    reason = _verdict_reason(verdict_text)
    label = "Passed" if passed else "Rejected"
    summary = f"{label} — {reason}" if reason else label
    if agent_run is not None:
        agent_run.result_summary = summary[:2000]
        await _clear_run_artifacts(session, agent_run.id)
    if agent is not None and (agent_run is None or agent.current_run_id == agent_run.id):
        # Keep the mirror consistent with the run whose state it reflects.
        agent.result_summary = summary[:2000]
    if agent_run is None and agent is not None:
        await _clear_agent_artifacts(session, agent.id)
    await session.commit()


def _ordered_steps(workflow: Workflow) -> list[WorkflowStep]:
    return sorted(workflow.steps, key=lambda s: s.position)


def _is_runnable(step: WorkflowStep) -> bool:
    """Can the coordinator drive into this step?

    Normally that means it has an agent — but an ``approval`` step is driven by a
    *human*, so it's runnable with no agent attached. A step whose agent was
    deleted stays un-runnable and is skipped rather than stalling the run.
    """
    return step.kind == "approval" or step.agent_id is not None


def _first_runnable(steps: list[WorkflowStep]) -> WorkflowStep | None:
    for step in steps:
        if _is_runnable(step):
            return step
    return None


def _prev_runnable_idx(steps: list[WorkflowStep], idx: int) -> int | None:
    """Index of the nearest runnable step before ``idx`` (skips broken steps)."""
    for j in range(idx - 1, -1, -1):
        if _is_runnable(steps[j]):
            return j
    return None


def _next_runnable_idx(steps: list[WorkflowStep], idx: int) -> int | None:
    for j in range(idx + 1, len(steps)):
        if _is_runnable(steps[j]):
            return j
    return None


def _prev_content_idx(steps: list[WorkflowStep], idx: int) -> int | None:
    """Index of the nearest **content-producing** step before ``idx``.

    Gates are *transparent* to the data flow: they judge the material handed to
    them and emit only a terse verdict (``PASS: …``), so forwarding a gate's own
    output downstream would drop the real payload (e.g. the story a later step
    must record). Approval steps are transparent for the same reason — a human
    saying "yes" produces no content. This skips both (and missing-agent steps)
    to find the last step that actually produced content — so a
    `task → gate → task` chain feeds the second task the first task's output,
    not the gate's verdict.
    """
    for j in range(idx - 1, -1, -1):
        if steps[j].agent_id is not None and steps[j].kind in WORKFLOW_PRODUCING_KINDS:
            return j
    return None


def _earlier_content_agent_ids(steps: list[WorkflowStep], before_idx: int | None) -> list[int]:
    """Agent ids of content-producing steps strictly before ``before_idx`` (oldest first).

    Feeds the workflow blackboard: everything published *before* the immediate
    previous producer, so a later step inherits the whole accumulated trail of
    artifacts — not just the last hand-off. Gates and approval steps (neither of
    which publishes deliverables) and missing-agent steps are skipped.
    ``before_idx`` is the index of the immediate producer; ``None`` means there is
    no prior producer, so no trail.
    """
    if before_idx is None:
        return []
    out: list[int] = []
    for j in range(0, before_idx):
        step = steps[j]
        if step.agent_id is not None and step.kind in WORKFLOW_PRODUCING_KINDS:
            out.append(step.agent_id)
    return out


def _step_label(step: WorkflowStep) -> str:
    if step.name:
        return step.name
    if step.agent is not None and step.agent.title:
        return step.agent.title
    if step.kind == "approval":
        return "Human approval"
    return f"Step {step.position + 1}"


async def _build_context(
    session: AsyncSession,
    workflow: Workflow,
    step: WorkflowStep,
    prev_agent_id: int | None,
    *,
    earlier_agent_ids: list[int] | None = None,
    fail_reason: str | None = None,
    human_feedback: str | None = None,
    unblock_guidance: str | None = None,
    blocked_question: str | None = None,
    run_input: str | None = None,
    approval_notes: str | None = None,
    workflow_run_id: int | None = None,
) -> str | None:
    """Assemble a step's kickoff preamble: the run's brief, reviewer directives,
    rejection/loop-back reason, previous output, earlier-step artifacts (the
    accumulated blackboard), the workflow's saved-state index, a gate
    instruction, and the step's own mandate — in that reading order. Returns
    ``None`` when empty."""
    parts: list[str] = []
    if run_input:
        parts.append(_run_input_preamble(run_input))
    if approval_notes:
        parts.append(_approval_notes_preamble(approval_notes))
    if unblock_guidance:
        parts.append(_unblock_preamble(unblock_guidance, blocked_question))
    if human_feedback:
        parts.append(_rejection_preamble(human_feedback))
    if fail_reason:
        parts.append(_fail_preamble(fail_reason))
    # ``clear_artifacts`` decides the blackboard's lifetime. Set (the default),
    # each run starts clean, so we narrow the reads to this workflow run — which
    # is also what stops a concurrent workflow driving the same agent from
    # leaking its deliverables in here. Unset, the board is deliberately
    # cumulative across runs, so we read agent-wide as before runs existed.
    board_run_id = workflow_run_id if workflow.clear_artifacts else None
    if prev_agent_id is not None:
        ctx = await collect_step_context(session, prev_agent_id, workflow_run_id=board_run_id)
        if ctx:
            parts.append(ctx)
    if earlier_agent_ids:
        prior = await collect_prior_artifacts(
            session, earlier_agent_ids, workflow_run_id=board_run_id
        )
        if prior:
            parts.append(prior)
    # The pipeline's own memory, as a **key index only**. A step that wants a
    # specific value names it in a ``{{state.<key>}}`` placeholder (already
    # substituted below) or fetches it with ``workflow_state_get``; inlining
    # every body here would put the whole store in every step's context.
    if step.use_mcp is not False:
        state_index = await build_state_index_prompt(session, workflow.id)
        if state_index:
            parts.append(state_index)
    if step.kind == "gate":
        parts.append(_GATE_PREAMBLE)
    else:
        # A task step must complete autonomously — never stall the run asking the
        # (absent) human what to do. Always appended, even for a first step with
        # no upstream input, so no task step can block for clarification.
        parts.append(_TASK_PREAMBLE)
    # The step's own mandate goes last: it's the actionable directive, and the
    # closing lines of a preamble carry the most weight. Placeholders are
    # resolved here — the agent is handed real values, never a raw template.
    if step.instructions and step.instructions.strip():
        rendered = await render_step_instructions(
            session, workflow.id, workflow.current_run_id, step.instructions
        )
        parts.append(_step_instructions_preamble(rendered))
    return "\n\n---\n\n".join(parts) if parts else None


async def _run_input(session: AsyncSession, workflow: Workflow) -> str | None:
    """The brief the human (or webhook) supplied when starting the current run.

    Read back from the run row rather than threaded through every call site, so a
    step reached many advances later — or re-driven by a gate loop-back — still
    sees the same brief the run started with.
    """
    if workflow.current_run_id is None:
        return None
    run = await session.get(WorkflowRun, workflow.current_run_id)
    return (run.input or None) if run is not None else None


# The placeholder stored when someone approves without typing anything — not a
# directive, so it must never be forwarded as one.
_BARE_APPROVAL = "Approved."


async def _approval_notes(session: AsyncSession, workflow: Workflow) -> str | None:
    """Notes reviewers attached when clearing approval checkpoints in this run.

    An approval step is *transparent to the data flow* — it publishes no content,
    so the joke (not the reviewer's remark) is what flows downstream. But a note
    like "translate it into French before sending" is a **directive**, and it
    would be lost if we only skipped the step. We read the notes back off the
    run's own approval traces — no extra state — and hand them to every
    subsequent step, so an instruction still applies when the step it's aimed at
    is two hops later.
    """
    if workflow.current_run_id is None:
        return None
    rows = await session.execute(
        select(WorkflowRunStep.label, WorkflowRunStep.output_summary)
        .where(
            WorkflowRunStep.run_id == workflow.current_run_id,
            WorkflowRunStep.kind == "approval",
            WorkflowRunStep.status == "passed",
            WorkflowRunStep.output_summary.is_not(None),
        )
        .order_by(WorkflowRunStep.id)
    )
    notes = [
        (label, (summary or "").strip())
        for label, summary in rows.all()
        if (summary or "").strip() and (summary or "").strip() != _BARE_APPROVAL
    ]
    if not notes:
        return None
    return "\n\n".join(f"- {text}" if len(notes) > 1 else text for _label, text in notes)


# --- Run-trace recording ---------------------------------------------------
# The coordinator appends a durable trace as it drives a run: one WorkflowRun per
# execution, one WorkflowRunStep per step *attempt*. These helpers keep the
# recording concerns out of the advancement logic and are all no-ops when the
# workflow has no active run (e.g. legacy rows), so tracing never blocks a run.


async def _begin_run(
    session: AsyncSession,
    workflow: Workflow,
    trigger: str,
    run_input: str | None = None,
) -> None:
    """Open a fresh run-trace and point the workflow at it."""
    run = WorkflowRun(
        workflow_id=workflow.id,
        run_number=workflow.run_count or 1,
        status="running",
        trigger=trigger if trigger in WORKFLOW_RUN_TRIGGERS else "manual",
        started_at=datetime.now(UTC),
        input=(run_input.strip()[:8000] or None) if run_input else None,
    )
    session.add(run)
    await session.flush()
    workflow.current_run_id = run.id


async def _step_output(
    session: AsyncSession,
    agent_id: int,
    agent_run: AgentRun | None = None,
) -> str | None:
    """A trace-worthy snapshot of what a step produced: its result summary, or —
    when a bare turn left none — its (directive-stripped) assistant body.

    Prefers the run's own summary; the agent's is a mirror that a concurrent
    execution may already have overwritten."""
    if agent_run is not None and agent_run.result_summary:
        return agent_run.result_summary
    if agent_run is None:
        agent = await session.get(AgentSession, agent_id)
        if agent is not None and agent.result_summary:
            return agent.result_summary
    body = await _last_assistant_message(
        session, agent_id, agent_run.id if agent_run is not None else None
    )
    if body:
        return _DIRECTIVE_LINE_RE.sub("", body).strip() or body
    return None


async def _open_run_step(
    session: AsyncSession, run_id: int, position: int, agent_id: int
) -> WorkflowRunStep | None:
    """The still-open trace for one step attempt, if there is one.

    A step entry opens exactly one of these and the advance that handles its
    turn closes it, which makes it the natural idempotency token: an advance
    that finds none has already been beaten to this turn by another. A manual
    replay's trace is excluded — it is not a turn the run is waiting on, so
    consuming it here would let a replay swallow the pipeline's own advance.
    """
    result = await session.execute(
        select(WorkflowRunStep)
        .where(
            WorkflowRunStep.run_id == run_id,
            WorkflowRunStep.position == position,
            WorkflowRunStep.agent_id == agent_id,
            WorkflowRunStep.replay.is_(False),
            WorkflowRunStep.finished_at.is_(None),
        )
        .order_by(WorkflowRunStep.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _started_after(run_step: WorkflowRunStep, moment: datetime) -> bool:
    """Did this attempt begin after ``moment``? (SQLite hands back naive times.)"""
    started = run_step.started_at
    if started is None:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return started > moment


async def _supersede_open_run_steps(session: AsyncSession, run_id: int, position: int) -> None:
    """Close any trace left open at ``position`` before a new attempt opens one.

    Should never fire now that advances are serialised, but a database written
    by an older build (or a process killed mid-step) carries rows stuck at
    ``running`` with no ``finished_at``. Left alone they render as a step that is
    forever in flight, and they'd sit in front of a manual retry's own trace.
    """
    rows = (
        (
            await session.execute(
                select(WorkflowRunStep).where(
                    WorkflowRunStep.run_id == run_id,
                    WorkflowRunStep.position == position,
                    WorkflowRunStep.finished_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.status = "superseded"
        row.finished_at = row.started_at or datetime.now(UTC)


async def _snapshot_step_overrides(run: AgentRun, workflow: Workflow, step: WorkflowStep) -> None:
    """Bake the step's (and workflow's) overrides into the run's own snapshot.

    Agents are shared, reusable rows, so a step's capability choices and the
    workflow's Assistant Role belong to *this execution*, not to the definition.
    Writing them onto the run means two workflows can drive the same agent with
    different toggles at the same time, and an edit to the definition mid-run
    can't change what an in-flight step is executing with. A ``None`` override
    leaves the run's inherited setting alone — except for the MCP server scope,
    which is always assigned (see below).
    """
    if step.use_mcp is not None:
        run.use_mcp = step.use_mcp
    if step.use_skills is not None:
        run.use_skills = step.use_skills
    if step.use_memory is not None:
        run.use_memory = step.use_memory
    # Unlike the toggles above, the server scope is assigned even when null:
    # null means "the whole enabled catalogue", not "whatever the definition
    # happens to carry". Without this a narrow step would silently inherit a
    # scope it never asked for.
    run.mcp_servers = step.mcp_servers
    if workflow.role_id is not None:
        run.role_id = workflow.role_id
    if workflow.approval_policy is not None:
        # The pipeline's tool-approval stance wins for the duration of the run.
        # On the run rather than the agent, so a shared agent keeps its own
        # policy everywhere else it is used — including in a concurrent workflow.
        run.approval_policy = workflow.approval_policy


async def _open_agent_run(
    session: AsyncSession,
    agent_id: int,
    workflow: Workflow,
    step: WorkflowStep | None,
    *,
    trigger: str = "workflow",
    workflow_run_id: int | None = None,
) -> AgentRun | None:
    """Open the :class:`AgentRun` this step will execute as.

    Created here rather than in the manager because the run id has to land on the
    ``WorkflowRunStep`` trace in the same transaction — the trace is the link
    between "attempt 2 of step 3" and "the execution that produced it".

    ``step`` is optional so a replay can still open a run when the definition has
    been edited out from under it; the agent's current settings then stand in for
    the vanished step's overrides.
    """
    agent = await session.get(AgentSession, agent_id)
    if agent is None:
        return None
    run = AgentRun(
        agent_id=agent.id,
        trigger=trigger,
        workflow_run_id=workflow_run_id,
        status="pending",
        model=agent.model,
        use_mcp=agent.use_mcp,
        use_skills=agent.use_skills,
        use_memory=agent.use_memory,
        mcp_servers=agent.mcp_servers,
        approval_policy=agent.approval_policy,
        role_id=agent.role_id,
        started_at=datetime.now(UTC),
    )
    if step is not None:
        await _snapshot_step_overrides(run, workflow, step)
    session.add(run)
    await session.flush()
    agent.current_run_id = run.id
    return run


async def _current_run_for(
    session: AsyncSession,
    agent_id: int,
    workflow_run_id: int | None,
) -> AgentRun | None:
    """The newest run this agent opened for a given workflow run."""
    if workflow_run_id is None:
        return None
    result = await session.execute(
        select(AgentRun)
        .where(AgentRun.agent_id == agent_id, AgentRun.workflow_run_id == workflow_run_id)
        .order_by(AgentRun.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _launch_step(
    session: AsyncSession,
    manager: AgentManager,
    workflow: Workflow,
    step: WorkflowStep,
    context: str | None,
) -> None:
    """Open the step's agent run, record its trace, then enqueue it.

    The single choke point through which every step is started, so every attempt
    — including gate loop-backs — leaves a trace with the exact input it saw and
    the exact execution that ran it.
    """
    agent_id = step.agent_id
    if agent_id is None:
        # ``_enter_step`` never routes an agent-less step here; be explicit.
        return

    run_id = workflow.current_run_id
    agent_run = await _open_agent_run(session, agent_id, workflow, step, workflow_run_id=run_id)
    if agent_run is None:
        return

    if run_id is not None:
        await _supersede_open_run_steps(session, run_id, step.position)
        prior = await session.execute(
            select(func.count(WorkflowRunStep.id)).where(
                WorkflowRunStep.run_id == run_id,
                WorkflowRunStep.position == step.position,
                # Manual replays are numbered on their own sequence, so a replay
                # never inflates "the pipeline drove this step N times".
                WorkflowRunStep.replay.is_(False),
            )
        )
        attempt = int(prior.scalar() or 0) + 1
        run_step = WorkflowRunStep(
            run_id=run_id,
            position=step.position,
            kind=step.kind,
            label=_step_label(step),
            agent_id=agent_id,
            agent_run_id=agent_run.id,
            attempt=attempt,
            status="running",
            input_context=context,
            started_at=datetime.now(UTC),
            # The run starts at zero, so its own counters *are* the attempt's
            # spend — no baseline arithmetic against a shared cumulative total,
            # and no cross-attribution when two workflows share an agent.
            token_baseline_in=0,
            token_baseline_out=0,
        )
        session.add(run_step)
        await session.flush()
        agent_run.workflow_run_step_id = run_step.id
    await session.commit()
    manager.enqueue(manager.start_task(agent_id, extra_context=context, run_id=agent_run.id))


async def _close_run_step(
    session: AsyncSession,
    run_step: WorkflowRunStep,
    *,
    status: str,
    output_summary: str | None = None,
    gate_verdict: str | None = None,
) -> None:
    """Stamp one trace row with its outcome, spend, and finish time.

    Shared by the pipeline's advance and by a manual replay, so a replayed
    attempt is accounted for exactly like the turn it re-runs.
    """
    run_step.status = status
    run_step.finished_at = datetime.now(UTC)
    if output_summary is not None:
        run_step.output_summary = output_summary[:8000]
    if gate_verdict is not None:
        run_step.gate_verdict = gate_verdict

    agent = await session.get(AgentSession, run_step.agent_id) if run_step.agent_id else None
    agent_run = (
        await session.get(AgentRun, run_step.agent_run_id) if run_step.agent_run_id else None
    )

    # Keep the agent's Agents-section unread badge clear for turns it ran *as a
    # workflow step*: the workflow is the coordinator here, so these replies
    # aren't the "autonomous agent finished something for you" signal the badge
    # is meant to convey. Advancing ``last_read_at`` past this step's events
    # marks them read without touching genuinely autonomous (manual/scheduled)
    # runs, whose events land after their own ``last_read_at``.
    if agent is not None:
        agent.last_read_at = run_step.finished_at

    # Cost accounting: this attempt's spend is its *own* run's counters. Baselines
    # are kept for rows written before runs existed (and stay at zero for new
    # ones), so a legacy trace still subtracts correctly. Clamped at zero because
    # a run whose context was cleared mid-flight can see its totals reset.
    if agent_run is not None:
        run_step.input_tokens = max(
            0, (agent_run.total_input_tokens or 0) - run_step.token_baseline_in
        )
        run_step.output_tokens = max(
            0, (agent_run.total_output_tokens or 0) - run_step.token_baseline_out
        )
    elif agent is not None:
        run_step.input_tokens = max(0, agent.total_input_tokens - run_step.token_baseline_in)
        run_step.output_tokens = max(0, agent.total_output_tokens - run_step.token_baseline_out)
    if agent_run is not None or agent is not None:
        run = await session.get(WorkflowRun, run_step.run_id)
        if run is not None:
            run.total_input_tokens = (run.total_input_tokens or 0) + run_step.input_tokens
            run.total_output_tokens = (run.total_output_tokens or 0) + run_step.output_tokens


async def _finalize_run_step(
    session: AsyncSession,
    workflow: Workflow,
    agent_id: int,
    *,
    status: str,
    output_summary: str | None = None,
    gate_verdict: str | None = None,
) -> None:
    """Close out the still-open trace for this agent in the current run."""
    run_id = workflow.current_run_id
    if run_id is None:
        return
    result = await session.execute(
        select(WorkflowRunStep)
        .where(
            WorkflowRunStep.run_id == run_id,
            WorkflowRunStep.agent_id == agent_id,
            # A replay's trace is closed by the replay seam, never by an advance.
            WorkflowRunStep.replay.is_(False),
            WorkflowRunStep.finished_at.is_(None),
        )
        .order_by(WorkflowRunStep.id.desc())
        .limit(1)
    )
    run_step = result.scalar_one_or_none()
    if run_step is None:
        return
    await _close_run_step(
        session,
        run_step,
        status=status,
        output_summary=output_summary,
        gate_verdict=gate_verdict,
    )


async def _finalize_run(
    session: AsyncSession,
    workflow: Workflow,
    *,
    status: str,
    result_summary: str | None = None,
    error: str | None = None,
) -> None:
    """Stamp the run row with its terminal (or paused) outcome."""
    run_id = workflow.current_run_id
    if run_id is None:
        return
    run = await session.get(WorkflowRun, run_id)
    if run is None:
        return
    run.status = status
    if status in ("completed", "failed", "cancelled"):
        run.finished_at = datetime.now(UTC)
    else:
        # Re-opened (resumed or retried): a run that is going again hasn't
        # finished, and must not keep a stale outcome from the attempt that
        # stopped it.
        run.finished_at = None
        run.error = None
    if result_summary is not None:
        run.result_summary = result_summary[:2000]
    if error is not None:
        run.error = error[:2000]


# --- Step entry / advancement ------------------------------------------------
# Every path that moves a run onto a step funnels through ``_enter_step`` — the
# first step of a run, a forward advance, a gate loop-back, a failure retry, an
# approval resume. That's what lets an ``approval`` step (which has no agent and
# must park for a human) work identically wherever it sits in the pipeline.


async def _open_approval_trace(
    session: AsyncSession, workflow: Workflow, step: WorkflowStep
) -> None:
    """Record the trace row for an approval checkpoint the run is parking on."""
    run_id = workflow.current_run_id
    if run_id is None:
        return
    prior = await session.execute(
        select(func.count(WorkflowRunStep.id)).where(
            WorkflowRunStep.run_id == run_id,
            WorkflowRunStep.position == step.position,
        )
    )
    session.add(
        WorkflowRunStep(
            run_id=run_id,
            position=step.position,
            kind="approval",
            label=_step_label(step),
            agent_id=None,
            attempt=int(prior.scalar() or 0) + 1,
            status="awaiting_approval",
            input_context=step.instructions,
            started_at=datetime.now(UTC),
        )
    )


async def _finalize_step_by_position(
    session: AsyncSession,
    workflow: Workflow,
    position: int,
    *,
    status: str,
    output_summary: str | None = None,
) -> None:
    """Close an open trace addressed by *position* rather than agent.

    Approval traces carry no ``agent_id``, so ``_finalize_run_step`` (which looks
    a trace up by its agent) can't reach them.
    """
    run_id = workflow.current_run_id
    if run_id is None:
        return
    result = await session.execute(
        select(WorkflowRunStep)
        .where(
            WorkflowRunStep.run_id == run_id,
            WorkflowRunStep.position == position,
            WorkflowRunStep.replay.is_(False),
            WorkflowRunStep.finished_at.is_(None),
        )
        .order_by(WorkflowRunStep.id.desc())
        .limit(1)
    )
    run_step = result.scalar_one_or_none()
    if run_step is None:
        return
    run_step.status = status
    run_step.finished_at = datetime.now(UTC)
    if output_summary is not None:
        run_step.output_summary = output_summary[:8000]


def _selected_source_positions(step: WorkflowStep, max_position: int) -> list[int]:
    """Parse ``context_sources`` into valid, ordered, de-duplicated positions."""
    raw = (step.context_sources or "").replace(";", ",")
    out: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk.isdigit():
            continue
        pos = int(chunk)
        if 0 <= pos <= max_position and pos not in out:
            out.append(pos)
    return sorted(out)


async def _enter_step(
    session: AsyncSession,
    manager: AgentManager,
    workflow: Workflow,
    steps: list[WorkflowStep],
    idx: int,
    *,
    fail_reason: str | None = None,
    human_feedback: str | None = None,
    unblock_guidance: str | None = None,
) -> None:
    """Drive the run into ``steps[idx]``: park for a human, or launch its agent."""
    step = steps[idx]
    workflow.current_step_id = step.id

    if step.kind == "approval":
        # Hand the wheel to the human and stop. Nothing runs until they decide.
        workflow.status = "awaiting_approval"
        await _open_approval_trace(session, workflow, step)
        await _finalize_run(session, workflow, status="awaiting_approval")
        await session.commit()
        await _publish(workflow)
        return

    if step.agent_id is None:
        return

    # What the agent parked itself on, read *before* the launch clears it, so the
    # guidance below can quote the question it answers.
    blocked_question: str | None = None
    if unblock_guidance:
        parked = await session.get(AgentSession, step.agent_id)
        blocked_question = parked.blocked_question if parked else None

    # Capability overrides are snapshotted onto the run in ``_launch_step`` below,
    # not written onto the agent — the definition is shared, the execution isn't.
    await session.commit()
    await _publish(workflow)

    # What this step inherits. ``auto`` is the implicit pipeline hand-off;
    # ``selected`` narrows it to named earlier steps; ``none`` cuts it off so the
    # step runs on its own objective (plus the run brief) alone.
    mode = step.context_mode or "auto"
    prev_agent_id: int | None = None
    earlier_ids: list[int] = []
    if mode == "selected":
        wanted = _selected_source_positions(step, max(0, len(steps) - 1))
        sources = [
            s.agent_id
            for pos in wanted
            for s in steps
            if s.position == pos and s.agent_id is not None and pos != step.position
        ]
        if sources:
            # Newest selection is the immediate hand-off; the rest form the board.
            prev_agent_id = sources[-1]
            earlier_ids = sources[:-1]
    elif mode != "none":
        prev_idx = _prev_content_idx(steps, idx)
        prev_agent_id = steps[prev_idx].agent_id if prev_idx is not None else None
        earlier_ids = _earlier_content_agent_ids(steps, prev_idx)

    context = await _build_context(
        session,
        workflow,
        step,
        prev_agent_id,
        earlier_agent_ids=earlier_ids,
        fail_reason=fail_reason,
        human_feedback=human_feedback,
        unblock_guidance=unblock_guidance,
        blocked_question=blocked_question,
        run_input=await _run_input(session, workflow),
        approval_notes=await _approval_notes(session, workflow),
        workflow_run_id=workflow.current_run_id,
    )
    await _launch_step(session, manager, workflow, step, context)


async def _complete_run(
    session: AsyncSession,
    workflow: Workflow,
    *,
    last_agent_id: int | None,
    last_agent_run: AgentRun | None = None,
) -> None:
    """Finish the pipeline: stamp the workflow and run with the final deliverable."""
    workflow.status = "completed"
    workflow.finished_at = datetime.now(UTC)
    workflow.current_step_id = None
    # The final step's *run* holds the deliverable; the agent row only mirrors
    # whichever execution touched it last, which for a shared agent may be
    # another workflow's.
    if last_agent_run is not None and last_agent_run.result_summary:
        workflow.result_summary = last_agent_run.result_summary[:2000]
    elif last_agent_id is not None:
        summary = await _last_run_summary(session, last_agent_id, workflow.current_run_id)
        if summary:
            workflow.result_summary = summary[:2000]
    await _finalize_run(
        session, workflow, status="completed", result_summary=workflow.result_summary
    )
    await session.commit()
    await _publish(workflow)


async def _advance_from(
    session: AsyncSession,
    manager: AgentManager,
    workflow: Workflow,
    steps: list[WorkflowStep],
    from_idx: int,
    *,
    last_agent_id: int | None = None,
    last_agent_run: AgentRun | None = None,
) -> None:
    """Move to the next runnable step after ``from_idx``, or finish the run."""
    next_idx = _next_runnable_idx(steps, from_idx)
    if next_idx is None:
        await _complete_run(
            session, workflow, last_agent_id=last_agent_id, last_agent_run=last_agent_run
        )
        return
    await _enter_step(session, manager, workflow, steps, next_idx)


async def _apply_failure_policy(
    session: AsyncSession,
    manager: AgentManager,
    workflow: Workflow,
    steps: list[WorkflowStep],
    idx: int,
    agent_id: int,
    reason: str,
) -> None:
    """Handle a failed (or timed-out) step according to its ``on_error`` policy.

    ``retry`` re-drives the same step with the failure reason injected, up to
    ``max_retries``. ``continue`` records the failure and carries on — for steps
    whose output is optional. ``fail`` (and an exhausted retry budget) stops the
    whole run, which is the conservative default.
    """
    step = steps[idx]
    policy = step.on_error or "fail"

    if policy == "retry" and (step.retry_count or 0) < (step.max_retries or 0):
        step.retry_count = (step.retry_count or 0) + 1
        await _finalize_run_step(
            session, workflow, agent_id, status="failed", output_summary=reason
        )
        await session.commit()
        await _publish(workflow)
        await _enter_step(
            session,
            manager,
            workflow,
            steps,
            idx,
            fail_reason=f"The previous attempt failed: {reason}",
        )
        return

    if policy == "continue":
        await _finalize_run_step(
            session, workflow, agent_id, status="failed", output_summary=reason
        )
        await session.commit()
        await _publish(workflow)
        # The failed step produced nothing usable, so hand the *previous*
        # producer's output onward rather than a dead end.
        await _advance_from(session, manager, workflow, steps, idx, last_agent_id=None)
        return

    workflow.status = "failed"
    workflow.finished_at = datetime.now(UTC)
    workflow.current_step_id = None
    workflow.error = reason[:2000]
    await _finalize_run_step(session, workflow, agent_id, status="failed", output_summary=reason)
    await _finalize_run(session, workflow, status="failed", error=workflow.error)
    await session.commit()
    await _publish(workflow)


async def start_workflow(
    session: AsyncSession,
    manager: AgentManager,
    workflow_id: int,
    *,
    trigger: str = "manual",
    run_input: str | None = None,
) -> Workflow | None:
    """Kick off a run: reset run state and start the first step's agent.

    ``run_input`` is an optional per-run brief (the file to analyse, the topic to
    research, a webhook payload). It's stored on the run and prepended to every
    step's kickoff preamble. Omit it and the pipeline runs autonomously on its
    steps' own objectives, exactly as before.

    Returns the updated workflow, or ``None`` if it has no runnable step. Safe to
    call on an idle/completed/draft workflow; refuses if already running.
    """
    workflow = await _load_workflow(session, workflow_id)
    if workflow is None:
        return None
    if workflow.status == "running":
        return workflow

    steps = _ordered_steps(workflow)
    first = _first_runnable(steps)
    if first is None:
        # Nothing to run — leave as draft.
        workflow.status = "draft"
        await session.commit()
        await _publish(workflow)
        return workflow

    now = datetime.now(UTC)
    workflow.status = "running"
    workflow.current_step_id = first.id
    workflow.run_count = (workflow.run_count or 0) + 1
    workflow.last_run_at = now
    workflow.finished_at = None
    workflow.error = None
    workflow.result_summary = None

    # Reset per-run counters so gate loop caps and retry budgets apply per run,
    # not cumulatively across the workflow's lifetime.
    for step in steps:
        step.attempt_count = 0
        step.retry_count = 0

    # Runs start clean by construction: each step opens a fresh ``AgentRun`` and
    # artifacts are scoped to it, so nothing needs pre-clearing. Wiping the
    # agent-wide rows here would destroy a concurrent workflow's blackboard —
    # exactly the bleed the run split exists to stop. ``clear_artifacts`` keeps
    # its meaning at the *read* side instead (see ``_build_context``): set, a
    # step sees only this run's board; unset, it sees every run's.

    await _begin_run(session, workflow, trigger, run_input)
    await session.commit()
    await _publish(workflow)

    first_idx = next((i for i, s in enumerate(steps) if s.id == first.id), 0)
    await _enter_step(session, manager, workflow, steps, first_idx)
    return workflow


async def advance_for_run(session: AsyncSession, manager: AgentManager, agent_run_id: int) -> None:
    """Completion-seam hook: advance the workflow this agent *run* belongs to.

    Called (via the manager) after a run's status commit reaches a resting or
    terminal state. A run belongs to exactly one workflow run, so this resolves
    a single workflow rather than fanning out across every workflow that happens
    to share the agent — the fan-out was the reason one agent going idle could
    advance two unrelated pipelines at once.

    Runs under the per-workflow advance lock, and everything it decides on is
    re-read **inside** that lock. The seam is fire-and-forget, so a second
    advance for the same run is routinely already queued behind this one; by the
    time it acquires the lock the run has moved on, the match below fails, and it
    returns having done nothing — instead of re-entering the same step.

    ``entered_at`` carries the moment this advance was asked for, which settles
    the case the moved-on cursor can't: a step re-entered *in place* (an
    ``on_error=retry`` loop-back) leaves the cursor where it was and opens a new
    trace, which a duplicate would otherwise happily consume. An advance is only
    ever a response to a turn that had already started, so it may not act on an
    attempt that began after it did.
    """
    entered_at = datetime.now(UTC)
    agent_run = await session.get(AgentRun, agent_run_id)
    if agent_run is None:
        return
    # Read the run's identity out now: ``expire_all`` below invalidates the
    # instance, and a lazy re-read of an expired attribute is IO in a place
    # SQLAlchemy's async layer can't await.
    agent_id = agent_run.agent_id
    launched_for_run = agent_run.workflow_run_id

    # A manual replay runs outside the pipeline, so no advance will ever close
    # its trace. Handled here because this is the one seam every ended turn
    # reaches, and it is independent of the workflow match below (a replay only
    # happens when no run is active, so that match is empty by definition).
    # Isolated behind a rollback: a half-written replay close must not leave a
    # dirty session for the advance that follows it.
    try:
        await finalize_replays_for_run(session, agent_run_id)
    except Exception:
        logger.debug("failed to close replay traces for run %s", agent_run_id, exc_info=True)
        await session.rollback()

    # A run knows the workflow run it was launched for, so there is exactly one
    # candidate. A run with no ``workflow_run_id`` (manual, scheduled, fleet) has
    # nothing to advance.
    if launched_for_run is None:
        return
    wf_run = await session.get(WorkflowRun, launched_for_run)
    if wf_run is None:
        return
    workflow_id = wf_run.workflow_id

    async with _workflow_lock(workflow_id):
        # Re-read under the lock. ``expire_all`` drops anything this session
        # cached before waiting, so a workflow another advance just moved on
        # isn't re-advanced off a stale copy.
        session.expire_all()
        workflow = await _load_workflow(session, workflow_id)
        if workflow is None or workflow.status != "running":
            return
        # Only the run that is *currently* driving the workflow may advance it: a
        # superseded attempt finishing late must not move the cursor.
        if workflow.current_run_id != launched_for_run:
            return
        current = next(
            (s for s in _ordered_steps(workflow) if s.id == workflow.current_step_id), None
        )
        if current is None or current.agent_id != agent_id:
            # The run has already been advanced past this agent's turn.
            return
        fresh = await session.get(AgentRun, agent_run_id)
        status = fresh.status if fresh else "failed"
        await _advance_one(
            session,
            manager,
            workflow,
            agent_id,
            status,
            entered_at=entered_at,
            agent_run_id=agent_run_id,
        )
        # Every terminal path in ``_advance_one`` commits, but flush anything
        # a future one might leave pending: the next advance waiting on this
        # lock must not re-read a step entry that hasn't landed yet.
        await session.commit()


async def _advance_one(
    session: AsyncSession,
    manager: AgentManager,
    workflow: Workflow,
    agent_id: int,
    agent_status: str,
    *,
    entered_at: datetime | None = None,
    agent_run_id: int | None = None,
) -> None:
    steps = _ordered_steps(workflow)
    # Locate the current step by id, then its index.
    current_idx = next((i for i, s in enumerate(steps) if s.id == workflow.current_step_id), None)
    if current_idx is None:
        return

    # This step's open trace is the turn's idempotency token: whoever handles the
    # turn closes it, so a duplicate advance finds nothing open and stops here.
    # ``entered_at`` covers the one case that leaves a *fresh* row in its place —
    # a step re-entered in place by ``on_error=retry``, which reopens a trace at
    # the same position before the duplicate gets to look. An advance always
    # answers a turn that was already under way, so a row that started after the
    # advance did belongs to the next attempt, not this one. A run-less workflow
    # has no trace to consume and is exempt.
    run_id = workflow.current_run_id
    if run_id is not None:
        step_agent_id = steps[current_idx].agent_id
        if step_agent_id is not None:
            open_trace = await _open_run_step(
                session, run_id, steps[current_idx].position, step_agent_id
            )
            if open_trace is None:
                return
            if entered_at is not None and _started_after(open_trace, entered_at):
                return

    # The execution's own state, never the agent's mirror: a shared agent's row
    # may already be reporting whatever a concurrent workflow is doing to it.
    agent_run = await session.get(AgentRun, agent_run_id) if agent_run_id else None

    now = datetime.now(UTC)

    if agent_status in STEP_FAILED_STATUSES:
        if agent_run is not None and agent_run.error:
            reason = agent_run.error
        else:
            agent = await session.get(AgentSession, agent_id)
            reason = agent.error if agent and agent.error else "A step failed."
        await _apply_failure_policy(
            session, manager, workflow, steps, current_idx, agent_id, reason
        )
        return

    if agent_status in STEP_CANCELLED_STATUSES:
        workflow.status = "cancelled"
        workflow.finished_at = now
        workflow.current_step_id = None
        await _finalize_run_step(session, workflow, agent_id, status="cancelled")
        await _finalize_run(session, workflow, status="cancelled")
        await session.commit()
        await _publish(workflow)
        return

    if agent_status in STEP_AWAITING_PERMISSION_STATUSES:
        # A tool-permission gate inside a live turn. The agent picks up where it
        # left off as soon as the decision lands, so there is nothing to resume
        # and nothing to record: leave the run ``running`` with its step trace
        # open and just publish, which surfaces the approve/deny card on the
        # board. The step timeout still applies, so a gate nobody ever answers is
        # caught by the watchdog rather than parking the pipeline forever.
        await _publish(workflow)
        return

    if agent_status in STEP_BLOCKED_STATUSES:
        # The agent raised a question and ended its turn. Hold the workflow
        # paused on this step so a resume re-drives it once answered.
        workflow.status = "paused"
        await _finalize_run_step(session, workflow, agent_id, status="blocked")
        await _finalize_run(session, workflow, status="paused")
        await session.commit()
        await _publish(workflow)
        return

    if agent_status not in STEP_DONE_STATUSES:
        # running / interrupted / pending — not a decision point.
        return

    current = steps[current_idx]

    # --- Gate step: parse PASS/FAIL and loop back on FAIL ------------------
    if current.kind == "gate":
        agent = await session.get(AgentSession, agent_id)
        # Read the raw archived turn first: the manager cleans directive tokens out
        # of ``result_summary`` for display, so the raw message is the reliable
        # source of the PASS/FAIL verdict. Fall back to the summary if unavailable.
        verdict_text = (
            await _last_assistant_message(
                session, agent_id, agent_run.id if agent_run is not None else None
            )
            or ""
        )
        if not verdict_text:
            fallback = (
                agent_run.result_summary
                if agent_run is not None and agent_run.result_summary
                else (agent.result_summary if agent and agent.result_summary else "")
            )
            verdict_text = fallback or ""
        passed = _parse_verdict(verdict_text)
        # Normalise the gate's stored result to a plain verdict and drop its
        # auto-captured artifact — a gate judges, it doesn't publish deliverables.
        await _tidy_gate_result(session, agent, agent_run, verdict_text, passed)
        if not passed:
            # Where to retry: explicit on_fail_position, else the previous step.
            target_idx = current.on_fail_position
            if (
                target_idx is None
                or target_idx < 0
                or target_idx >= len(steps)
                or not _is_runnable(steps[target_idx])
            ):
                target_idx = _prev_runnable_idx(steps, current_idx)
            if target_idx is None:
                # Nothing to loop back to — a gate with no upstream is a hard fail.
                workflow.status = "failed"
                workflow.finished_at = now
                workflow.current_step_id = None
                workflow.error = (
                    f"Gate '{_step_label(current)}' failed and has no earlier step to retry: "
                    f"{_verdict_reason(verdict_text)}"
                )[:2000]
                await _finalize_run_step(
                    session,
                    workflow,
                    agent_id,
                    status="failed",
                    output_summary=_verdict_reason(verdict_text),
                    gate_verdict="FAIL",
                )
                await _finalize_run(session, workflow, status="failed", error=workflow.error)
                await session.commit()
                await _publish(workflow)
                return

            # Count this loop-back on the gate; give up once the cap is exceeded.
            current.attempt_count = (current.attempt_count or 0) + 1
            if current.attempt_count > (workflow.max_loops or 3):
                workflow.status = "failed"
                workflow.finished_at = now
                workflow.current_step_id = None
                workflow.error = (
                    f"Gate '{_step_label(current)}' still failing after "
                    f"{workflow.max_loops} attempts: {_verdict_reason(verdict_text)}"
                )[:2000]
                await _finalize_run_step(
                    session,
                    workflow,
                    agent_id,
                    status="failed",
                    output_summary=_verdict_reason(verdict_text),
                    gate_verdict="FAIL",
                )
                await _finalize_run(session, workflow, status="failed", error=workflow.error)
                await session.commit()
                await _publish(workflow)
                return

            await _finalize_run_step(
                session,
                workflow,
                agent_id,
                status="failed",
                output_summary=_verdict_reason(verdict_text),
                gate_verdict="FAIL",
            )
            await _enter_step(
                session,
                manager,
                workflow,
                steps,
                target_idx,
                fail_reason=_verdict_reason(verdict_text),
            )
            return
        # PASS → close the gate trace, then fall through to the forward advance.
        await _finalize_run_step(
            session,
            workflow,
            agent_id,
            status="passed",
            output_summary=_verdict_reason(verdict_text),
            gate_verdict="PASS",
        )

    # --- Step done: close its trace, then move on (or finish) --------------
    # A just-passed gate already closed its own trace above.
    if current.kind != "gate":
        await _finalize_run_step(
            session,
            workflow,
            agent_id,
            status="completed",
            output_summary=await _step_output(session, agent_id, agent_run),
        )
    await _advance_from(
        session,
        manager,
        workflow,
        steps,
        current_idx,
        last_agent_id=agent_id,
        last_agent_run=agent_run,
    )


async def pause_workflow(session: AsyncSession, workflow_id: int) -> Workflow | None:
    """Hold a running workflow between steps (the active agent keeps its turn;
    advancement is suppressed while paused)."""
    workflow = await _load_workflow(session, workflow_id)
    if workflow is None:
        return None
    if workflow.status == "running":
        workflow.status = "paused"
        await session.commit()
        await _publish(workflow)
    return workflow


async def resume_workflow(
    session: AsyncSession,
    manager: AgentManager,
    workflow_id: int,
    *,
    guidance: str | None = None,
) -> Workflow | None:
    """Resume a paused workflow by re-driving its current step's agent.

    ``guidance`` is the answer to whatever parked the run. A pause is usually
    just a pause, but a run also parks when a step's agent **blocks** on a
    question — and re-driving that step unchanged would strand it on the same
    question. Supplying guidance injects the answer into the step's kickoff so
    the retry can actually get past it.
    """
    workflow = await _load_workflow(session, workflow_id)
    if workflow is None:
        return None
    if workflow.status != "paused":
        return workflow

    steps = _ordered_steps(workflow)
    current = next((s for s in steps if s.id == workflow.current_step_id), None)
    if current is None or not _is_runnable(current):
        current = _first_runnable(steps)
    if current is None:
        return workflow

    workflow.status = "running"
    # Re-open the paused run trace so the resumed attempt records under it.
    await _finalize_run(session, workflow, status="running")

    # ``_enter_step`` re-derives the step's context (and parks again if the
    # resumed step is an approval checkpoint).
    idx = next((i for i, s in enumerate(steps) if s.id == current.id), 0)
    await _enter_step(
        session,
        manager,
        workflow,
        steps,
        idx,
        unblock_guidance=(guidance.strip() if guidance and guidance.strip() else None),
    )
    return workflow


async def resolve_step_permission(
    session: AsyncSession,
    manager: AgentManager,
    workflow_id: int,
    *,
    request_id: str,
    decision: str,
) -> tuple[Workflow | None, bool]:
    """Answer the tool-permission gate parking a step, and let the run carry on.

    Resolving the gate in the runtime is only half of it. The block paused the
    *workflow* and closed the step's trace, and ``advance_for_run`` only ever
    looks at ``running`` workflows — so an approved agent would happily finish
    its turn into a pipeline that had stopped listening, leaving the board stuck
    on "Blocked" forever. Putting the run back to ``running`` and opening a trace
    for the continuing attempt restores the seam, so the coordinator advances
    when the turn lands.

    Returns ``(workflow, resolved)``. ``resolved`` is False when no live request
    matched — a stale card from a gate that has since been cancelled or answered
    elsewhere — so the caller can say so rather than silently doing nothing.
    """
    workflow = await _load_workflow(session, workflow_id)
    if workflow is None:
        return None, False

    steps = _ordered_steps(workflow)
    # The parked agent is whichever step is holding the run. Fall back to
    # scanning, because the cursor can lag a publish.
    current = next((s for s in steps if s.id == workflow.current_step_id), None)
    candidates = [current] if current and current.agent_id else [s for s in steps if s.agent_id]
    resolved = False
    agent_id: int | None = None
    for step in candidates:
        if step is None or step.agent_id is None:
            continue
        if await manager.resolve_permission(step.agent_id, request_id, decision):
            resolved = True
            agent_id = step.agent_id
            target = step
            break
    if not resolved or agent_id is None:
        return workflow, False

    if workflow.status == "paused":
        workflow.status = "running"
        workflow.current_step_id = target.id
        await _finalize_run(session, workflow, status="running")
        # The blocked attempt's trace was closed when the run parked; the turn
        # now continuing is a new one, so give it somewhere to record.
        run_id = workflow.current_run_id
        if run_id is not None:
            prior = await session.execute(
                select(func.count(WorkflowRunStep.id)).where(
                    WorkflowRunStep.run_id == run_id,
                    WorkflowRunStep.position == target.position,
                )
            )
            # The gate resumed the *same* execution, so the continuing attempt
            # baselines against that run's counters as they stand right now.
            agent_run = await _current_run_for(session, agent_id, run_id)
            session.add(
                WorkflowRunStep(
                    run_id=run_id,
                    position=target.position,
                    kind=target.kind,
                    label=_step_label(target),
                    agent_id=agent_id,
                    agent_run_id=agent_run.id if agent_run is not None else None,
                    attempt=int(prior.scalar() or 0) + 1,
                    status="running",
                    input_context=(
                        f"Permission {'granted' if decision != 'deny' else 'denied'} by a human; "
                        "the step continued from where it stopped."
                    ),
                    started_at=datetime.now(UTC),
                    token_baseline_in=(
                        (agent_run.total_input_tokens or 0) if agent_run is not None else 0
                    ),
                    token_baseline_out=(
                        (agent_run.total_output_tokens or 0) if agent_run is not None else 0
                    ),
                )
            )
        await session.commit()
        await _publish(workflow)
    return workflow, True


async def retry_step(
    session: AsyncSession,
    manager: AgentManager,
    workflow_id: int,
    *,
    position: int | None = None,
    guidance: str | None = None,
) -> Workflow | None:
    """Re-drive one step of a stopped run as a fresh attempt, in place.

    When a step fails under the ``fail`` policy the whole run stops — and the
    only way back was to run the pipeline again from step 1, throwing away every
    good step before the bad one (and paying for them twice). This picks the run
    back up at the step that broke: it re-enters that step, which appends a new
    attempt to the *same* run trace, and carries on through the rest of the
    pipeline from there.

    ``position`` targets a specific step; omitted, it retries the one that
    stopped the run. ``guidance`` is optional human input injected into the
    retry, for a failure the agent can't diagnose on its own.
    """
    workflow = await _load_workflow(session, workflow_id)
    if workflow is None:
        return None
    # Only a stopped run can be picked back up: retrying a step underneath a
    # live run would race the coordinator that is still driving it.
    if workflow.status not in ("failed", "cancelled"):
        return workflow

    steps = _ordered_steps(workflow)
    if not steps:
        return workflow

    target_idx: int | None = None
    if position is not None:
        target_idx = next((i for i, s in enumerate(steps) if s.position == position), None)
    else:
        failed_pos = await _last_failed_position(session, workflow)
        if failed_pos is not None:
            target_idx = next((i for i, s in enumerate(steps) if s.position == failed_pos), None)
    if target_idx is None:
        return workflow

    step = steps[target_idx]
    # An approval checkpoint has no agent to re-drive; re-entering it just parks
    # the run for the human again, which is the right behaviour.
    if not _is_runnable(step) and step.kind != "approval":
        return workflow

    # A retry is a fresh attempt at the same step, so the exhausted automatic
    # retry budget resets — otherwise the manual retry would inherit a spent
    # counter and give up immediately on its next failure.
    step.retry_count = 0
    workflow.status = "running"
    workflow.error = None
    workflow.finished_at = None
    await _finalize_run(session, workflow, status="running")
    await session.commit()
    await _publish(workflow)

    await _enter_step(
        session,
        manager,
        workflow,
        steps,
        target_idx,
        unblock_guidance=(guidance.strip() if guidance and guidance.strip() else None),
    )
    return workflow


# --- Manual replay ----------------------------------------------------------
# Retrying is about *recovering a run*: it re-drives the step that stopped the
# pipeline and carries on through everything after it. Replaying is about
# *interrogating a step*: an operator reading a finished trace wants to see what
# this one step does when handed the very same input again — a different sample
# from a non-deterministic model, or a second look after tweaking the agent's
# prompt or its tools. So a replay deliberately does none of the coordination:
# it never moves ``current_step_id``, never reopens the run, and nothing advances
# when it ends. It appends one clearly-marked attempt to the run it belongs to,
# which is why it works just as well on a run that succeeded.

# An agent in one of these is mid-turn; replaying would hand it a second prompt
# on top of the one it is already answering.
_REPLAY_BUSY_STATUSES = ("running", "needs_approval", "pending")


async def replay_step(
    session: AsyncSession,
    manager: AgentManager,
    workflow_id: int,
    *,
    step_run_id: int,
) -> tuple[Workflow | None, str | None]:
    """Re-run one recorded step attempt on its own, with the input it first saw.

    Returns ``(workflow, refusal)`` — ``workflow`` is ``None`` when the attempt
    doesn't belong to this workflow (a 404), and ``refusal`` carries a
    human-readable reason when the replay can't be honoured right now.
    """
    workflow = await _load_workflow(session, workflow_id)
    if workflow is None:
        return None, None

    run_step = await session.get(WorkflowRunStep, step_run_id)
    if run_step is None:
        return None, None
    run = await session.get(WorkflowRun, run_step.run_id)
    if run is None or run.workflow_id != workflow_id:
        return None, None

    # A run that hasn't finished still owns its steps' agents: replaying
    # underneath one would race the coordinator (or, for a paused run, collide
    # with the resume that re-drives the parked step) over the same agent.
    if workflow.status in ("running", "paused", "awaiting_approval"):
        return workflow, "Stop the run before replaying a step."
    if run_step.agent_id is None:
        return workflow, "This step ran no agent, so there's nothing to replay."

    agent = await session.get(AgentSession, run_step.agent_id)
    if agent is None:
        return workflow, "The agent that ran this step no longer exists."
    if agent.status in _REPLAY_BUSY_STATUSES:
        return workflow, "That step's agent is busy — wait for its current turn to end."

    # Re-apply the step's capability scope and the workflow's role onto a fresh
    # run, so the replay executes under the same conditions the pipeline gave it.
    # Best-effort: the definition may have been edited since, in which case the
    # agent's current settings stand.
    step = next(
        (
            s
            for s in _ordered_steps(workflow)
            if s.position == run_step.position and s.agent_id == run_step.agent_id
        ),
        None,
    )
    # Unconditional: a replay with no run would leave a trace row that
    # ``finalize_replays_for_run`` (which matches on ``agent_run_id``) can never
    # close, pinning the step "in flight" on the board forever.
    agent_run = await _open_agent_run(
        session,
        run_step.agent_id,
        workflow,
        step,
        trigger="replay",
        workflow_run_id=run_step.run_id,
    )

    prior_replays = await session.execute(
        select(func.count(WorkflowRunStep.id)).where(
            WorkflowRunStep.run_id == run_step.run_id,
            WorkflowRunStep.position == run_step.position,
            WorkflowRunStep.replay.is_(True),
        )
    )
    context = run_step.input_context
    replay_step = WorkflowRunStep(
        run_id=run_step.run_id,
        position=run_step.position,
        kind=run_step.kind,
        label=run_step.label,
        agent_id=run_step.agent_id,
        agent_run_id=agent_run.id if agent_run is not None else None,
        attempt=int(prior_replays.scalar() or 0) + 1,
        replay=True,
        status="running",
        input_context=context,
        started_at=datetime.now(UTC),
        token_baseline_in=0,
        token_baseline_out=0,
    )
    session.add(replay_step)
    await session.flush()
    if agent_run is not None:
        agent_run.workflow_run_step_id = replay_step.id
    await session.commit()
    await _publish(workflow)

    manager.enqueue(
        manager.start_task(
            run_step.agent_id,
            extra_context=context,
            run_id=agent_run.id if agent_run is not None else None,
        )
    )
    return workflow, None


async def finalize_replays_for_run(session: AsyncSession, agent_run_id: int) -> None:
    """Close the open replay trace this agent *run* was driving, if any.

    A replay is invisible to the coordinator by design, so nothing else would
    ever close its row — it would render as a step forever in flight. This runs
    off the same completion seam as the advance, but independently of it: a
    replay happens precisely when no run is active, which is exactly when the
    advance has nothing to do.

    Matched by run rather than by agent so a shared agent's *other* execution
    can't close a replay it never started.
    """
    agent_run = await session.get(AgentRun, agent_run_id)
    if agent_run is None:
        return
    agent_id = agent_run.agent_id
    run_step = (
        await session.execute(
            select(WorkflowRunStep)
            .where(
                WorkflowRunStep.agent_run_id == agent_run_id,
                WorkflowRunStep.replay.is_(True),
                WorkflowRunStep.finished_at.is_(None),
            )
            .order_by(WorkflowRunStep.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if run_step is None:
        return

    agent = await session.get(AgentSession, agent_id)
    status = agent_run.status
    if status in STEP_AWAITING_PERMISSION_STATUSES:
        # The turn is still alive and resumes the moment the gate is answered.
        return

    if status in STEP_FAILED_STATUSES:
        await _close_run_step(session, run_step, status="failed")
    elif status in STEP_CANCELLED_STATUSES:
        await _close_run_step(session, run_step, status="cancelled")
    elif status in STEP_BLOCKED_STATUSES:
        await _close_run_step(session, run_step, status="blocked")
    elif status in STEP_DONE_STATUSES:
        if run_step.kind == "gate":
            verdict_text = await _last_assistant_message(
                session, agent_id, agent_run.id if agent_run is not None else None
            ) or (agent_run.result_summary or "")
            passed = _parse_verdict(verdict_text)
            await _tidy_gate_result(session, agent, agent_run, verdict_text, passed)
            await _close_run_step(
                session,
                run_step,
                status="passed" if passed else "failed",
                output_summary=_verdict_reason(verdict_text),
                gate_verdict="PASS" if passed else "FAIL",
            )
        else:
            await _close_run_step(
                session,
                run_step,
                status="completed",
                output_summary=await _step_output(session, agent_id),
            )
    else:
        # running / interrupted — not a decision point.
        return

    await session.commit()
    run = await session.get(WorkflowRun, run_step.run_id)
    workflow = await _load_workflow(session, run.workflow_id) if run is not None else None
    if workflow is not None:
        await _publish(workflow)


async def step_attempt_events(
    session: AsyncSession, workflow_id: int, step_run_id: int
) -> list[dict[str, Any]] | None:
    """The agent's normalised event stream for one step *attempt*.

    A trace row tells you what a step received and produced, but not *how* it
    got there — which is exactly what you need when a step blocks or stalls with
    no output at all. The events (tool calls, reasoning, errors) live per agent
    in ``agent_events``, so an agent re-driven four times has one continuous
    stream; this slices it to the attempt's own window so each row shows only
    its own work.

    Returns ``None`` when the attempt doesn't belong to the workflow (a 404 for
    the caller), and an empty list when it simply has no archived activity.
    """
    run_step = await session.get(WorkflowRunStep, step_run_id)
    if run_step is None:
        return None
    run = await session.get(WorkflowRun, run_step.run_id)
    if run is None or run.workflow_id != workflow_id:
        return None
    if run_step.agent_id is None or run_step.started_at is None:
        return []

    # A small lead-in: the trace row is written immediately *before* the agent is
    # enqueued, but clock granularity (and a re-used session's first event)
    # shouldn't drop the opening events of the attempt.
    start = run_step.started_at - timedelta(seconds=2)
    stmt = (
        select(AgentEventRecord.payload, AgentEventRecord.created_at)
        .where(
            AgentEventRecord.agent_session_id == run_step.agent_id,
            AgentEventRecord.created_at >= start,
        )
        .order_by(AgentEventRecord.id)
    )
    if run_step.finished_at is not None:
        # A finished attempt is bounded — but not at exactly ``finished_at``.
        # The events that *cause* the finalization (the permission request that
        # blocked it, the error that failed it) are archived microseconds after
        # the status change that closed the row, so cutting there drops the very
        # evidence you opened the trace for. Extend to the next attempt on this
        # agent instead, which is the true boundary; only when there is no next
        # attempt do we fall back to a grace window.
        next_start = (
            await session.execute(
                select(WorkflowRunStep.started_at)
                .where(
                    WorkflowRunStep.agent_id == run_step.agent_id,
                    WorkflowRunStep.id != run_step.id,
                    WorkflowRunStep.started_at > run_step.started_at,
                )
                .order_by(WorkflowRunStep.started_at)
                .limit(1)
            )
        ).scalar_one_or_none()
        end = next_start or (run_step.finished_at + timedelta(seconds=30))
        stmt = stmt.where(AgentEventRecord.created_at < end)

    events: list[dict[str, Any]] = []
    for payload, created_at in (await session.execute(stmt)).all():
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        # Older rows predate the payload carrying its own timestamp; fall back to
        # the row's so the UI can always order and label them.
        if not data.get("at") and created_at is not None:
            data["at"] = created_at.isoformat()
        events.append(data)
    return events


async def _last_failed_position(session: AsyncSession, workflow: Workflow) -> int | None:
    """Position of the step whose failure stopped the current run.

    Read from the run trace rather than ``current_step_id``, which the failure
    path clears on its way out.
    """
    if workflow.current_run_id is None:
        return None
    result = await session.execute(
        select(WorkflowRunStep.position)
        .where(
            WorkflowRunStep.run_id == workflow.current_run_id,
            WorkflowRunStep.status.in_(("failed", "cancelled")),
            # A replay that went badly didn't stop the run; it must not redirect
            # a retry away from the step that actually did.
            WorkflowRunStep.replay.is_(False),
        )
        .order_by(WorkflowRunStep.id.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return int(row) if row is not None else None


# --- Human approval ---------------------------------------------------------
# An ``approval`` step is the counterpart to a gate: a gate is an *agent* judging
# the work, this is a *human*. The run parks on it — nothing burns tokens while
# it waits — until someone approves (carry on) or rejects with feedback (loop
# back and redo, reusing the gate's loop-back machinery and cap).


async def approve_step(
    session: AsyncSession,
    manager: AgentManager,
    workflow_id: int,
    *,
    note: str | None = None,
) -> Workflow | None:
    """Clear a human approval checkpoint and let the pipeline continue."""
    workflow = await _load_workflow(session, workflow_id)
    if workflow is None:
        return None
    if workflow.status != "awaiting_approval":
        return workflow

    steps = _ordered_steps(workflow)
    idx = next((i for i, s in enumerate(steps) if s.id == workflow.current_step_id), None)
    if idx is None:
        return workflow

    workflow.status = "running"
    await _finalize_step_by_position(
        session,
        workflow,
        steps[idx].position,
        status="passed",
        output_summary=(note.strip() if note and note.strip() else "Approved."),
    )
    await _finalize_run(session, workflow, status="running")
    await session.commit()
    await _publish(workflow)

    # An approval produces no content, so the deliverable stays whatever the last
    # real producer made — which is what a final ``_complete_run`` should report.
    content_idx = _prev_content_idx(steps, idx)
    last_agent_id = steps[content_idx].agent_id if content_idx is not None else None
    await _advance_from(session, manager, workflow, steps, idx, last_agent_id=last_agent_id)
    return workflow


async def reject_step(
    session: AsyncSession,
    manager: AgentManager,
    workflow_id: int,
    *,
    feedback: str | None = None,
    action: str | None = None,
) -> Workflow | None:
    """Send the work back — or stop the run — from a human approval checkpoint.

    What happens next comes from the step's ``on_reject`` policy, which the
    reviewer may override per decision via ``action``:

    * ``rework`` — mirror a failing gate: loop back to ``on_fail_position``
      (default: the previous producing step) with the feedback injected, bounded
      by the workflow's ``max_loops`` cap.
    * ``stop`` — end the run here. A deliberate human "no", recorded as
      ``cancelled`` rather than ``failed``: nothing broke, someone decided.
    * ``skip`` — drop the rejected work and carry on to the next step.
    """
    workflow = await _load_workflow(session, workflow_id)
    if workflow is None:
        return None
    if workflow.status != "awaiting_approval":
        return workflow

    steps = _ordered_steps(workflow)
    idx = next((i for i, s in enumerate(steps) if s.id == workflow.current_step_id), None)
    if idx is None:
        return workflow

    current = steps[idx]
    note = (feedback or "").strip() or "A reviewer rejected this and asked for a new version."
    policy = action if action in WORKFLOW_STEP_REJECT_POLICIES else (current.on_reject or "rework")

    if policy == "stop":
        workflow.status = "cancelled"
        workflow.finished_at = datetime.now(UTC)
        workflow.current_step_id = None
        workflow.result_summary = f"Stopped at '{_step_label(current)}': {note}"[:2000]
        await _finalize_step_by_position(
            session, workflow, current.position, status="failed", output_summary=note
        )
        await _finalize_run(session, workflow, status="cancelled", result_summary=note)
        await session.commit()
        await _publish(workflow)
        return workflow

    if policy == "skip":
        workflow.status = "running"
        await _finalize_step_by_position(
            session, workflow, current.position, status="skipped", output_summary=note
        )
        await _finalize_run(session, workflow, status="running")
        await session.commit()
        await _publish(workflow)
        # The rejected work is abandoned, so the next step inherits the last real
        # producer's output rather than anything from this checkpoint.
        content_idx = _prev_content_idx(steps, idx)
        last_agent_id = steps[content_idx].agent_id if content_idx is not None else None
        await _advance_from(session, manager, workflow, steps, idx, last_agent_id=last_agent_id)
        return workflow

    target_idx = current.on_fail_position
    if (
        target_idx is None
        or target_idx < 0
        or target_idx >= len(steps)
        or not _is_runnable(steps[target_idx])
        or target_idx == idx
    ):
        target_idx = _prev_runnable_idx(steps, idx)

    if target_idx is None:
        workflow.status = "failed"
        workflow.finished_at = datetime.now(UTC)
        workflow.current_step_id = None
        workflow.error = (
            f"Rejected at '{_step_label(current)}' with no earlier step to redo: {note}"[:2000]
        )
        await _finalize_step_by_position(
            session, workflow, current.position, status="failed", output_summary=note
        )
        await _finalize_run(session, workflow, status="failed", error=workflow.error)
        await session.commit()
        await _publish(workflow)
        return workflow

    current.attempt_count = (current.attempt_count or 0) + 1
    if current.attempt_count > (workflow.max_loops or 3):
        workflow.status = "failed"
        workflow.finished_at = datetime.now(UTC)
        workflow.current_step_id = None
        workflow.error = (
            f"Rejected at '{_step_label(current)}' after {workflow.max_loops} attempts: {note}"
        )[:2000]
        await _finalize_step_by_position(
            session, workflow, current.position, status="failed", output_summary=note
        )
        await _finalize_run(session, workflow, status="failed", error=workflow.error)
        await session.commit()
        await _publish(workflow)
        return workflow

    workflow.status = "running"
    await _finalize_step_by_position(
        session, workflow, current.position, status="failed", output_summary=note
    )
    await _finalize_run(session, workflow, status="running")
    await _enter_step(session, manager, workflow, steps, target_idx, human_feedback=note)
    return workflow


# --- Stall watchdog ---------------------------------------------------------


async def sweep_stalled_steps(session: AsyncSession, manager: AgentManager) -> int:
    """Fail-forward any step stuck past its workflow's ``step_timeout_seconds``.

    An agent that never returns would otherwise park an unattended pipeline in
    ``running`` forever. The watchdog cancels the wedged agent and puts the step
    through its normal ``on_error`` policy, so a timeout can retry, be skipped, or
    stop the run exactly like any other failure. Opt-in per workflow (null
    timeout = no watchdog). Returns how many runs it intervened in.
    """
    now = datetime.now(UTC)
    result = await session.execute(
        select(Workflow.id, Workflow.step_timeout_seconds).where(
            Workflow.status == "running",
            Workflow.step_timeout_seconds.is_not(None),
            Workflow.current_run_id.is_not(None),
        )
    )
    swept = 0
    for workflow_id, step_timeout in result.all():
        timeout = step_timeout or 0
        if timeout <= 0:
            continue
        # Under the same lock as an advance: a step finishing right as its
        # timeout elapses would otherwise be both advanced and failed forward,
        # driving the next step twice.
        async with _workflow_lock(workflow_id):
            session.expire_all()
            workflow = await _load_workflow(session, workflow_id)
            if workflow is None or workflow.status != "running" or workflow.current_run_id is None:
                continue
            open_step = (
                await session.execute(
                    select(WorkflowRunStep)
                    .where(
                        WorkflowRunStep.run_id == workflow.current_run_id,
                        WorkflowRunStep.finished_at.is_(None),
                        WorkflowRunStep.agent_id.is_not(None),
                        # A replay isn't part of the run's progress, so it must
                        # not be mistaken for the step the pipeline is stuck on.
                        WorkflowRunStep.replay.is_(False),
                    )
                    .order_by(WorkflowRunStep.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if open_step is None or open_step.started_at is None:
                continue
            started = open_step.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if (now - started).total_seconds() < timeout:
                continue

            steps = _ordered_steps(workflow)
            idx = next((i for i, s in enumerate(steps) if s.id == workflow.current_step_id), None)
            agent_id = open_step.agent_id
            if idx is None or agent_id is None:
                continue

            # Stop the wedged agent before re-driving or moving past it, so a
            # retry doesn't race a turn that may still be alive somewhere.
            try:
                manager.enqueue(manager.cancel(agent_id, run_id=open_step.agent_run_id))
            except Exception:  # pragma: no cover - manager is best-effort here
                logger.exception("Watchdog could not cancel agent %s", agent_id)

            minutes = max(1, round(timeout / 60))
            await _apply_failure_policy(
                session,
                manager,
                workflow,
                steps,
                idx,
                agent_id,
                f"Step '{_step_label(steps[idx])}' stalled with no result for over {minutes} min "
                "and was stopped by the watchdog.",
            )
            await session.commit()
            swept += 1
    return swept


async def cancel_workflow(
    session: AsyncSession, manager: AgentManager, workflow_id: int
) -> Workflow | None:
    """Stop a workflow and cancel its in-flight step agent."""
    workflow = await _load_workflow(session, workflow_id)
    if workflow is None:
        return None

    steps = _ordered_steps(workflow)
    current = next((s for s in steps if s.id == workflow.current_step_id), None)

    workflow.status = "cancelled"
    workflow.finished_at = datetime.now(UTC)
    workflow.current_step_id = None
    cancelled_run_id: int | None = None
    if current is not None and current.agent_id is not None:
        # Resolve *this* workflow's execution before the trace closes; a shared
        # agent's current run may belong to a different pipeline.
        agent_run = await _current_run_for(session, current.agent_id, workflow.current_run_id)
        cancelled_run_id = agent_run.id if agent_run is not None else None
        await _finalize_run_step(session, workflow, current.agent_id, status="cancelled")
    await _finalize_run(session, workflow, status="cancelled")
    await session.commit()
    await _publish(workflow)

    if current is not None and current.agent_id is not None:
        manager.enqueue(manager.cancel(current.agent_id, run_id=cancelled_run_id))
    return workflow
