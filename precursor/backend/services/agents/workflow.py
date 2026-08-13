"""Workflow coordinator — drives a :class:`Workflow` through its steps.

The workflow is the coordinator (n8n/Temporal-style): agents stay plain,
reusable units and never reference each other. Starting a workflow runs step 0's
agent; when that agent *rests* (idle / completed) the manager's completion seam
calls :func:`advance_for_agent`, which starts the next step's agent — injecting
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
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from precursor.backend.models.agent_artifact import AgentArtifact
from precursor.backend.models.agent_event import AgentEventRecord
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


async def _last_assistant_message(session: AsyncSession, agent_id: int) -> str | None:
    """The newest persisted ``assistant_message`` body for an agent (uncapped).

    Tier-1 hand-off: an agent that ends its turn with ``OBJECTIVE_COMPLETE:`` has
    its ``result_summary`` folded to the terse directive summary, losing the full
    output. The durable event archive still holds the complete assistant message,
    so a bare generative step ("tell me a story") forwards its whole body to the
    next step even after the directive fold.
    """
    rows = await session.execute(
        select(AgentEventRecord.payload)
        .where(AgentEventRecord.agent_session_id == agent_id)
        .order_by(AgentEventRecord.id.desc())
        .limit(100)
    )
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


async def collect_step_context(session: AsyncSession, prev_agent_id: int) -> str:
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
    body = await _last_assistant_message(session, prev_agent_id)
    if body:
        body = _DIRECTIVE_LINE_RE.sub("", body).strip()
    if not body and agent.result_summary:
        body = agent.result_summary.strip()
    if body:
        parts.append(body)
    arts = await session.execute(
        select(AgentArtifact)
        .where(AgentArtifact.agent_id == prev_agent_id)
        .order_by(AgentArtifact.created_at.asc())
    )
    for art in arts.scalars().all():
        parts.append(f"[{art.title}]\n{art.content}".strip())
    if not parts:
        return ""
    label = agent.title or f"Step agent {prev_agent_id}"
    return (
        "You are one step in a workflow. Here is the output of the previous step. "
        "Use it as the input for your objective.\n\n"
        f"### From previous step: {label}\n" + "\n\n".join(parts)
    )


async def collect_prior_artifacts(session: AsyncSession, agent_ids: list[int]) -> str:
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
        arts = await session.execute(
            select(AgentArtifact)
            .where(AgentArtifact.agent_id == aid)
            .order_by(AgentArtifact.created_at.asc())
        )
        items = arts.scalars().all()
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


async def _tidy_gate_result(
    session: AsyncSession,
    agent: AgentSession | None,
    verdict_text: str,
    passed: bool,
) -> None:
    """Keep a gate's stored output clean — a gate is a judge, not a producer.

    A gate ends its turn with ``OBJECTIVE_COMPLETE: PASS/FAIL: <reason>``, which the
    manager folds verbatim into ``result_summary`` and auto-captures as a "Result"
    artifact. Left as-is that leaks the raw ``PASS:``/``FAIL:`` directive token into
    the step's displayed result and drops a non-deliverable verdict onto the shared
    blackboard. We rewrite the summary to a plain human phrasing and delete the
    gate's artifacts so downstream steps only ever inherit real content.
    """
    if agent is None:
        return
    reason = _verdict_reason(verdict_text)
    label = "Passed" if passed else "Rejected"
    summary = f"{label} — {reason}" if reason else label
    agent.result_summary = summary[:2000]
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
) -> str | None:
    """Assemble a step's kickoff preamble: the run's brief, reviewer directives,
    rejection/loop-back reason, previous output, earlier-step artifacts (the
    accumulated blackboard), a gate instruction, and the step's own mandate — in
    that reading order. Returns ``None`` when empty."""
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
    if prev_agent_id is not None:
        ctx = await collect_step_context(session, prev_agent_id)
        if ctx:
            parts.append(ctx)
    if earlier_agent_ids:
        prior = await collect_prior_artifacts(session, earlier_agent_ids)
        if prior:
            parts.append(prior)
    if step.kind == "gate":
        parts.append(_GATE_PREAMBLE)
    else:
        # A task step must complete autonomously — never stall the run asking the
        # (absent) human what to do. Always appended, even for a first step with
        # no upstream input, so no task step can block for clarification.
        parts.append(_TASK_PREAMBLE)
    # The step's own mandate goes last: it's the actionable directive, and the
    # closing lines of a preamble carry the most weight.
    if step.instructions and step.instructions.strip():
        parts.append(_step_instructions_preamble(step.instructions))
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


async def _step_output(session: AsyncSession, agent_id: int) -> str | None:
    """A trace-worthy snapshot of what a step produced: its result summary, or —
    when a bare turn left none — its (directive-stripped) assistant body."""
    agent = await session.get(AgentSession, agent_id)
    if agent is not None and agent.result_summary:
        return agent.result_summary
    body = await _last_assistant_message(session, agent_id)
    if body:
        return _DIRECTIVE_LINE_RE.sub("", body).strip() or body
    return None


async def _launch_step(
    session: AsyncSession,
    manager: AgentManager,
    workflow: Workflow,
    step: WorkflowStep,
    context: str | None,
) -> None:
    """Record a run-step trace (its input snapshot) then enqueue its agent.

    The single choke point through which every step is started, so every attempt
    — including gate loop-backs — leaves a trace with the exact input it saw.
    """
    agent_id = step.agent_id
    if agent_id is None:
        # ``_enter_step`` never routes an agent-less step here; be explicit.
        return

    run_id = workflow.current_run_id
    if run_id is not None:
        prior = await session.execute(
            select(func.count(WorkflowRunStep.id)).where(
                WorkflowRunStep.run_id == run_id,
                WorkflowRunStep.position == step.position,
            )
        )
        attempt = int(prior.scalar() or 0) + 1
        # Snapshot the agent's cumulative token counters so this attempt's spend
        # can be computed as a delta when it finalizes.
        agent = await session.get(AgentSession, agent_id)
        session.add(
            WorkflowRunStep(
                run_id=run_id,
                position=step.position,
                kind=step.kind,
                label=_step_label(step),
                agent_id=agent_id,
                attempt=attempt,
                status="running",
                input_context=context,
                started_at=datetime.now(UTC),
                token_baseline_in=agent.total_input_tokens if agent else 0,
                token_baseline_out=agent.total_output_tokens if agent else 0,
            )
        )
        await session.commit()
    manager.enqueue(manager.start_task(agent_id, extra_context=context))


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
    if gate_verdict is not None:
        run_step.gate_verdict = gate_verdict

    # Keep the agent's Agents-section unread badge clear for turns it ran *as a
    # workflow step*: the workflow is the coordinator here, so these replies
    # aren't the "autonomous agent finished something for you" signal the badge
    # is meant to convey. Advancing ``last_read_at`` past this step's events
    # marks them read without touching genuinely autonomous (manual/scheduled)
    # runs, whose events land after their own ``last_read_at``.
    agent = await session.get(AgentSession, agent_id)
    if agent is not None:
        agent.last_read_at = run_step.finished_at

    # Cost accounting: this attempt spent whatever the agent's cumulative
    # counters moved by since launch. Clamped at zero because an agent whose
    # context was cleared mid-run can see its totals reset.
    if agent is not None:
        run_step.input_tokens = max(0, agent.total_input_tokens - run_step.token_baseline_in)
        run_step.output_tokens = max(0, agent.total_output_tokens - run_step.token_baseline_out)
        run = await session.get(WorkflowRun, run_id)
        if run is not None:
            run.total_input_tokens = (run.total_input_tokens or 0) + run_step.input_tokens
            run.total_output_tokens = (run.total_output_tokens or 0) + run_step.output_tokens


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


async def _apply_step_overrides(
    session: AsyncSession, workflow: Workflow, step: WorkflowStep
) -> None:
    """Push the step's (and workflow's) overrides onto the agent before it runs.

    Agents are shared, reusable rows, so a step's capability choices and the
    workflow's Assistant Role are applied *as the step is launched* rather than
    baked into the agent. Only one step of a workflow runs at a time, so the
    agent always reflects the step currently driving it. A ``None`` override
    leaves the agent's own setting alone.
    """
    if step.agent_id is None:
        return
    agent = await session.get(AgentSession, step.agent_id)
    if agent is None:
        return
    if step.use_mcp is not None:
        agent.use_mcp = step.use_mcp
    if step.use_skills is not None:
        agent.use_skills = step.use_skills
    if step.use_memory is not None:
        agent.use_memory = step.use_memory
    if workflow.role_id is not None:
        agent.role_id = workflow.role_id
    if workflow.approval_policy is not None:
        # The pipeline's tool-approval stance wins for the duration of the run.
        # Applied here rather than written onto the agent permanently, so a
        # shared agent keeps its own policy everywhere else it is used.
        agent.approval_policy = workflow.approval_policy


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

    # Capability overrides + the workflow's role land on the agent before the
    # session is (re)built, so the toggles apply to the turn we're about to run.
    await _apply_step_overrides(session, workflow, step)
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
        step,
        prev_agent_id,
        earlier_agent_ids=earlier_ids,
        fail_reason=fail_reason,
        human_feedback=human_feedback,
        unblock_guidance=unblock_guidance,
        blocked_question=blocked_question,
        run_input=await _run_input(session, workflow),
        approval_notes=await _approval_notes(session, workflow),
    )
    await _launch_step(session, manager, workflow, step, context)


async def _complete_run(
    session: AsyncSession,
    workflow: Workflow,
    *,
    last_agent_id: int | None,
) -> None:
    """Finish the pipeline: stamp the workflow and run with the final deliverable."""
    workflow.status = "completed"
    workflow.finished_at = datetime.now(UTC)
    workflow.current_step_id = None
    if last_agent_id is not None:
        done_agent = await session.get(AgentSession, last_agent_id)
        if done_agent and done_agent.result_summary:
            workflow.result_summary = done_agent.result_summary[:2000]
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
) -> None:
    """Move to the next runnable step after ``from_idx``, or finish the run."""
    next_idx = _next_runnable_idx(steps, from_idx)
    if next_idx is None:
        await _complete_run(session, workflow, last_agent_id=last_agent_id)
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

    # When configured, pre-clear every step agent's artifacts so the whole strip
    # visually resets at run start (start_task also clears the running agent's
    # own artifacts just before it sends, so per-step freshness is guaranteed
    # regardless of this flag).
    if workflow.clear_artifacts:
        for step in steps:
            if step.agent_id is not None:
                await _clear_agent_artifacts(session, step.agent_id)

    await _begin_run(session, workflow, trigger, run_input)
    await session.commit()
    await _publish(workflow)

    first_idx = next((i for i, s in enumerate(steps) if s.id == first.id), 0)
    await _enter_step(session, manager, workflow, steps, first_idx)
    return workflow


async def advance_for_agent(session: AsyncSession, manager: AgentManager, agent_id: int) -> None:
    """Completion-seam hook: advance any running workflow parked on this agent.

    Called (via the manager) after an agent's status commit reaches a resting or
    terminal state. Finds the running workflow whose *current step* is this
    agent and either advances to the next step, completes, fails, pauses, or
    cancels the workflow accordingly.
    """
    # Which running workflows have their current step pointing at this agent?
    result = await session.execute(
        select(Workflow)
        .join(WorkflowStep, Workflow.current_step_id == WorkflowStep.id)
        .where(Workflow.status == "running", WorkflowStep.agent_id == agent_id)
        .options(selectinload(Workflow.steps).selectinload(WorkflowStep.agent))
    )
    workflows = result.scalars().unique().all()
    if not workflows:
        return

    agent = await session.get(AgentSession, agent_id)
    status = agent.status if agent else "failed"

    for workflow in workflows:
        await _advance_one(session, manager, workflow, agent_id, status)


async def _advance_one(
    session: AsyncSession,
    manager: AgentManager,
    workflow: Workflow,
    agent_id: int,
    agent_status: str,
) -> None:
    steps = _ordered_steps(workflow)
    # Locate the current step by id, then its index.
    current_idx = next((i for i, s in enumerate(steps) if s.id == workflow.current_step_id), None)
    if current_idx is None:
        return

    now = datetime.now(UTC)

    if agent_status in STEP_FAILED_STATUSES:
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
        verdict_text = await _last_assistant_message(session, agent_id) or ""
        if not verdict_text:
            verdict_text = (agent.result_summary if agent and agent.result_summary else "") or ""
        passed = _parse_verdict(verdict_text)
        # Normalise the gate's stored result to a plain verdict and drop its
        # auto-captured artifact — a gate judges, it doesn't publish deliverables.
        await _tidy_gate_result(session, agent, verdict_text, passed)
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
            output_summary=await _step_output(session, agent_id),
        )
    await _advance_from(session, manager, workflow, steps, current_idx, last_agent_id=agent_id)


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
    *workflow* and closed the step's trace, and ``advance_for_agent`` only ever
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
            agent = await session.get(AgentSession, agent_id)
            session.add(
                WorkflowRunStep(
                    run_id=run_id,
                    position=target.position,
                    kind=target.kind,
                    label=_step_label(target),
                    agent_id=agent_id,
                    attempt=int(prior.scalar() or 0) + 1,
                    status="running",
                    input_context=(
                        f"Permission {'granted' if decision != 'deny' else 'denied'} by a human; "
                        "the step continued from where it stopped."
                    ),
                    started_at=datetime.now(UTC),
                    token_baseline_in=agent.total_input_tokens if agent else 0,
                    token_baseline_out=agent.total_output_tokens if agent else 0,
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
        select(Workflow).where(
            Workflow.status == "running",
            Workflow.step_timeout_seconds.is_not(None),
            Workflow.current_run_id.is_not(None),
        )
    )
    swept = 0
    for row in result.scalars().unique().all():
        timeout = row.step_timeout_seconds or 0
        if timeout <= 0:
            continue
        open_step = (
            await session.execute(
                select(WorkflowRunStep)
                .where(
                    WorkflowRunStep.run_id == row.current_run_id,
                    WorkflowRunStep.finished_at.is_(None),
                    WorkflowRunStep.agent_id.is_not(None),
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

        workflow = await _load_workflow(session, row.id)
        if workflow is None:
            continue
        steps = _ordered_steps(workflow)
        idx = next((i for i, s in enumerate(steps) if s.id == workflow.current_step_id), None)
        agent_id = open_step.agent_id
        if idx is None or agent_id is None:
            continue

        # Stop the wedged agent before re-driving or moving past it, so a retry
        # doesn't race a turn that may still be alive somewhere.
        try:
            manager.enqueue(manager.cancel(agent_id))
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
    if current is not None and current.agent_id is not None:
        await _finalize_run_step(session, workflow, current.agent_id, status="cancelled")
    await _finalize_run(session, workflow, status="cancelled")
    await session.commit()
    await _publish(workflow)

    if current is not None and current.agent_id is not None:
        manager.enqueue(manager.cancel(current.agent_id))
    return workflow
