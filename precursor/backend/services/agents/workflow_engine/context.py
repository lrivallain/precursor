"""The kickoff context a step's agent receives: the preambles, the previous
steps' outputs and artifacts, the run input and approval notes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import aliased

from precursor.backend.models.agent_artifact import AgentArtifact
from precursor.backend.models.agent_run import AgentRun
from precursor.backend.models.agent_session import AgentSession
from precursor.backend.models.workflow import (
    Workflow,
    WorkflowRun,
    WorkflowRunStep,
    WorkflowStep,
)
from precursor.backend.services.agents.directives import strip_published_artifacts
from precursor.backend.services.workflow_state import (
    build_state_index_prompt,
    render_step_instructions,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


from precursor.backend.services.agents.workflow_engine.common import (
    _current_run_for,
    _has_precursor_tools,
    _last_assistant_message,
)
from precursor.backend.services.agents.workflow_engine.output import _DIRECTIVE_LINE_RE

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
# deliverable, rather than stalling to ask what to do. The artifact syntax is
# spelled out because a step that isn't autonomous never sees the full protocol.
_TASK_PREAMBLE = (
    "You are an automated step in a workflow — it runs unattended, so there is no "
    "human available to answer questions mid-run. Act fully autonomously: carry "
    "out YOUR objective directly, treating the material above as your input. Do "
    "NOT ask for clarification, present a menu of options, or request "
    "confirmation, and never emit NEED_INPUT — if a detail is underspecified, "
    "pick the most reasonable interpretation and proceed anyway. Produce the "
    "actual deliverable your objective calls for (not a description of what you "
    "could do), publish it as an artifact — a line 'ARTIFACT: <title>', the full "
    "deliverable on the lines below it, then a line 'END_ARTIFACT' — and end with "
    "'OBJECTIVE_COMPLETE: <2-3 sentence summary>'."
)


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


# Sentinel for ``collect_step_context(artifacts_run_id=…)`` meaning "mirror
# ``workflow_run_id``". Distinct from an explicit ``None``, which means "read the
# blackboard agent-wide" — the cumulative-board option a workflow opts into.
_MIRROR_RUN_SCOPE: Any = object()


def _superseded_attempt_runs() -> Any:
    """Agent runs a later attempt of the *same step* has replaced.

    Every step attempt — a gate loop-back, an unblock, a rejected approval's
    re-drive, a replay — opens a fresh ``AgentRun`` under the same workflow run
    and leaves a ``WorkflowRunStep`` trace at the step's position. An attempt is
    superseded once a newer trace at that ``(run_id, position)`` points at a
    different run. Keyed on the position rather than the agent, so an agent
    reused at two steps of one workflow keeps both steps' deliverables.
    """
    newer = aliased(WorkflowRunStep)
    return select(WorkflowRunStep.agent_run_id).where(
        WorkflowRunStep.agent_run_id.is_not(None),
        exists().where(
            newer.run_id == WorkflowRunStep.run_id,
            newer.position == WorkflowRunStep.position,
            newer.id > WorkflowRunStep.id,
            newer.agent_run_id.is_not(None),
            newer.agent_run_id != WorkflowRunStep.agent_run_id,
        ),
    )


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

    Either way, an attempt a later attempt of the same step superseded never
    reaches the board. Stacked, a gate that failed once would judge every
    earlier draft beside the current one and fail on the duplication itself
    until ``max_loops`` ran out.
    """
    stmt = select(AgentArtifact).where(
        AgentArtifact.agent_id == agent_id,
        or_(
            AgentArtifact.agent_run_id.is_(None),
            AgentArtifact.agent_run_id.not_in(_superseded_attempt_runs()),
        ),
    )
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
    artifacts_run_id: Any = _MIRROR_RUN_SCOPE,
) -> str:
    """Format the previous step's output + artifacts as a kickoff preamble.

    Workflow-specific (no agent-deps): the next step is fed *only* the immediately
    preceding step's output, mirroring a linear pipeline hand-off. Prefers the
    full assistant message (survives ``OBJECTIVE_COMPLETE`` folding) and falls
    back to ``result_summary``.

    The hand-off body is **strictly scoped to ``workflow_run_id``**. If the
    previous step opened no run here, or spoke and summarised nothing in the one
    it did open, this run simply has no output for it — and we say so rather than
    widening the read to the agent, which would forward an *earlier* run's output
    with nothing marking it stale. A step that then acts on it (declining a
    meeting, filing a ticket) acts on data nobody produced or reviewed in this
    run. The agent-wide read survives only for callers with no run in hand.

    ``artifacts_run_id`` scopes the blackboard separately and mirrors
    ``workflow_run_id`` unless given: a workflow may deliberately accumulate
    artifacts across runs (``clear_artifacts`` unset) without that also
    re-opening the hand-off body to earlier runs.
    """
    agent = await session.get(AgentSession, prev_agent_id)
    if agent is None:
        return ""
    board_run_id: int | None = (
        workflow_run_id if artifacts_run_id is _MIRROR_RUN_SCOPE else artifacts_run_id
    )
    parts: list[str] = []
    artifacts = await _run_scoped_artifacts(session, prev_agent_id, board_run_id)
    prev_run = await _current_run_for(session, prev_agent_id, workflow_run_id)
    if workflow_run_id is None or prev_run is not None:
        raw = await _last_assistant_message(
            session, prev_agent_id, prev_run.id if prev_run is not None else None
        )
        body = ""
        lifted = False
        if raw:
            # Artifacts follow as blackboard entries; don't forward them twice.
            kept = strip_published_artifacts(raw, {(a.title, a.content) for a in artifacts})
            lifted = kept != raw
            body = _DIRECTIVE_LINE_RE.sub("", kept).strip()
        # The summary would only repeat the lifted artifacts' bodies.
        if not body and not lifted:
            summary = await _prev_step_summary(session, prev_agent_id, workflow_run_id)
            if summary:
                body = summary.strip()
        if body:
            parts.append(body)
    for art in artifacts:
        parts.append(f"[{art.title}]\n{art.content}".strip())
    label = agent.title or f"Step agent {prev_agent_id}"
    if not parts:
        # No run scope means no run to be absent from — keep the legacy silence.
        return "" if workflow_run_id is None else _no_previous_output_preamble(label)
    return (
        "You are one step in a workflow. Here is the output of the previous step. "
        "Use it as the input for your objective.\n\n"
        f"### From previous step: {label}\n" + "\n\n".join(parts)
    )


def _no_previous_output_preamble(label: str) -> str:
    """Say plainly that the previous step produced nothing *in this run*.

    An explicit absence, not silence: a step told nothing at all is free to
    assume its input arrived some other way, and one told nothing about the
    absence may reach for whatever it last saw. Naming it lets the step reason
    about it and report the gap instead of inventing or reusing input.
    """
    return (
        "You are one step in a workflow. The previous step produced no output in this run.\n\n"
        f"### From previous step: {label}\n"
        "(no output this run)\n\n"
        "There is nothing to hand you: treat this as a real absence. Do not reuse anything "
        "from an earlier run of this workflow. Do whatever part of your objective stands "
        "without that input, and state plainly what you could not do and why."
    )


async def _prev_step_summary(
    session: AsyncSession,
    agent_id: int,
    workflow_run_id: int | None,
) -> str | None:
    """The previous step's result summary, scoped to its run when we have one.

    The agent's own ``result_summary`` is a mirror of whatever ran last on it —
    another workflow's turn for a shared agent, or simply this workflow's
    *previous* run. Falling back to it when the current run summarised nothing is
    how a step with no output silently inherits an older one's, so the agent-wide
    read is reserved for callers with no run in hand.
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
        return result.scalar_one_or_none()
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
    # ``clear_artifacts`` decides the **blackboard's** lifetime. Set (the default),
    # each run starts clean, so we narrow the artifact reads to this workflow run
    # — which is also what stops a concurrent workflow driving the same agent from
    # leaking its deliverables in here. Unset, the board is deliberately
    # cumulative across runs, so we read agent-wide as before runs existed.
    #
    # The immediate hand-off *body* is not part of that bargain: it is always
    # scoped to this run, so opting into a cumulative board never means a step
    # silently inherits an earlier run's output.
    board_run_id = workflow_run_id if workflow.clear_artifacts else None
    if prev_agent_id is not None:
        ctx = await collect_step_context(
            session,
            prev_agent_id,
            workflow_run_id=workflow_run_id,
            artifacts_run_id=board_run_id,
        )
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
    #
    # Gated on the step actually being able to *reach* those tools: the index
    # tells the agent to call ``workflow_state_get``, and a step scoped to, say,
    # ``workiq`` has no such tool. Advertising it there costs a wasted turn and
    # an agent explaining it cannot comply.
    if _has_precursor_tools(step):
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
