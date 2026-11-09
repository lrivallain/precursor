"""What a finished step produced: its output text, a gate's verdict, and the
tidying of a gate's result and artifacts.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import select

from precursor.backend.models.agent_artifact import AgentArtifact
from precursor.backend.models.agent_run import AgentRun
from precursor.backend.models.agent_session import AgentSession
from precursor.backend.services.agents.directives import (
    RESULT_SUMMARY_CAP,
    strip_control_directives,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


from precursor.backend.services.agents.workflow_engine.common import (
    _last_assistant_message,
)

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


# --- Run-trace recording ---------------------------------------------------
# The coordinator appends a durable trace as it drives a run: one WorkflowRun per
# execution, one WorkflowRunStep per step *attempt*. These helpers keep the
# recording concerns out of the advancement logic and are all no-ops when the
# workflow has no active run (e.g. legacy rows), so tracing never blocks a run.

# The trace's own ceiling for a step's output. Generous enough for a real payload
# — this is the channel ``{{step.N.output}}`` resolves from — but still finite so
# one runaway step can't bloat every run row.
OUTPUT_SUMMARY_CAP = 8000

# Appended when that ceiling actually bites. A severed payload is worse than a
# short one precisely because it looks complete: half a JSON array still parses.
# Saying so *in the text* is what lets the receiving step tell the difference.
_TRUNCATION_NOTE = "\n\n… [truncated: {total} characters, capped at {cap}]"


def _cap_output_summary(text: str) -> str:
    """Fit a step's output into the trace column, marking it when we had to cut."""
    if len(text) <= OUTPUT_SUMMARY_CAP:
        return text
    note = _TRUNCATION_NOTE.format(total=len(text), cap=OUTPUT_SUMMARY_CAP)
    return text[: OUTPUT_SUMMARY_CAP - len(note)] + note


async def _step_output(
    session: AsyncSession,
    agent_id: int,
    agent_run: AgentRun | None = None,
) -> str | None:
    """A trace-worthy snapshot of what a step produced: its result summary, or —
    when a bare turn left none — its (directive-stripped) assistant body.

    Prefers the run's own summary; the agent's is a mirror that a concurrent
    execution may already have overwritten.

    ``result_summary`` is capped at ``RESULT_SUMMARY_CAP`` for the *agent list*,
    which is far tighter than what the trace can hold. That cut must not leak
    here: this value is the trace's ``output_summary``, and therefore what
    ``{{step.N.output}}`` hands the next step — a data channel, not a card. A
    collector emitting a JSON array would otherwise arrive downstream severed
    mid-record, parsing as a shorter but perfectly valid array. So when the
    summary sits at the display cap we rebuild it from the durable event
    archive, scrubbed exactly the way the manager scrubbed the summary, and keep
    whichever is longer.
    """
    summary: str | None = None
    if agent_run is not None and agent_run.result_summary:
        summary = agent_run.result_summary
    elif agent_run is None:
        agent = await session.get(AgentSession, agent_id)
        if agent is not None and agent.result_summary:
            summary = agent.result_summary

    if summary is not None and len(summary) < RESULT_SUMMARY_CAP:
        return summary  # never reached the cap — nothing was lost

    body = await _last_assistant_message(
        session, agent_id, agent_run.id if agent_run is not None else None
    )
    if summary is not None:
        full = strip_control_directives(body) if body else ""
        return full if len(full) > len(summary) else summary
    if body:
        return _DIRECTIVE_LINE_RE.sub("", body).strip() or body
    return None


# A turn whose model stream drops mid-flight never emits ``turn_end`` or a
# ``usage`` event, so the manager just sees the session fall quiet and rests it
# at ``idle`` — indistinguishable, from the coordinator's seat, from a turn that
# finished. Left alone that reads as a clean success: the step is traced
# ``completed`` with an empty summary, the run advances, and the very failure
# ``on_error``/``max_retries`` exist for is out of their reach. Both halves of
# the test are required, and each covers the other's false positive: a step that
# genuinely answers nothing still burns tokens, and a turn that spent nothing
# still leaves text if it really spoke.
_DROPPED_TURN_REASON = (
    "The step's turn ended without producing any output or recording any model usage "
    "— the response stream most likely dropped mid-turn."
)


def _turn_dropped(agent_run: AgentRun | None, output: str | None) -> bool:
    """True when a nominally-done turn produced no output *and* recorded no spend."""
    if output and output.strip():
        return False
    # No execution row in hand means no usage counters to judge by — a caller
    # that can't see the spend must not guess a step into failure.
    if agent_run is None:
        return False
    return not (agent_run.total_input_tokens or 0) and not (agent_run.total_output_tokens or 0)
