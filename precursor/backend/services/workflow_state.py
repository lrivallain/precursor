"""Workflow state service — the pipeline's durable memory, and step placeholders.

Two jobs, kept together because they're two halves of the same feature:

* **the store** — read/upsert/delete a workflow's named values, with the same
  guardrails wherever a write comes from (the HTTP surface behind the UI panel,
  and the ``workflow_state_*`` MCP tools a running step calls);
* **the reader** — :func:`render_placeholders`, which substitutes
  ``{{state.<key>}}`` (plus ``{{run.input}}`` and ``{{step.<n>.output}}``) into a
  step's ``instructions`` before it's handed to the agent.

The placeholder half is what makes state usable *per step*: a pipeline keeps one
generic definition, and each step's mandate reads whichever named values it
actually needs, rather than every step inheriting the whole upstream transcript.

See :mod:`precursor.backend.models.workflow_state` for why this sits at workflow
scope rather than on the agent.
"""

from __future__ import annotations

import re

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import WorkflowRun, WorkflowRunStep, WorkflowState
from precursor.backend.models.workflow_state import WORKFLOW_STATE_MAX_KEYS
from precursor.backend.schemas.workflow_state import WorkflowStateSummary, WorkflowStateWrite

_STATE_PROMPT_HEADER = (
    "### This workflow's saved state\n"
    "Named values this pipeline keeps **across runs** — written by its steps, not by you "
    "alone. Bodies are not included here: read one with the `workflow_state_get` tool when "
    "you need it, and record anything a later step (or the next run) must know with "
    "`workflow_state_set`."
)

# What an unresolved placeholder renders as when no default was supplied. Chosen
# to read as an explicit absence in the middle of a sentence — the alternatives
# are worse: leaving the raw ``{{…}}`` invites the model to treat the template as
# literal text, and substituting an empty string silently changes the meaning of
# the instruction without anyone noticing.
UNSET_PLACEHOLDER = "(unset)"

# ``{{ name.parts | optional default }}``. The default is everything up to the
# closing brace, so it may contain spaces and punctuation but not ``}``.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(?P<expr>[A-Za-z0-9._-]+)\s*(?:\|(?P<default>[^}]*))?\}\}")
_STEP_OUTPUT_RE = re.compile(r"^step\.(?P<position>\d+)\.output$")


# --------------------------------------------------------------------- store


async def list_states(session: AsyncSession, workflow_id: int) -> list[WorkflowState]:
    """Every state entry for ``workflow_id``, key-ordered."""
    rows = await session.execute(
        select(WorkflowState)
        .where(WorkflowState.workflow_id == workflow_id)
        .order_by(WorkflowState.key)
    )
    return list(rows.scalars().all())


async def list_state_keys(session: AsyncSession, workflow_id: int) -> list[WorkflowStateSummary]:
    """The key index — keys, body sizes and mtimes, without the bodies."""
    rows = await session.execute(
        select(WorkflowState.key, func.length(WorkflowState.value), WorkflowState.updated_at)
        .where(WorkflowState.workflow_id == workflow_id)
        .order_by(WorkflowState.key)
    )
    return [
        WorkflowStateSummary(key=key, size=size or 0, updated_at=updated_at)
        for key, size, updated_at in rows.all()
    ]


async def get_state(session: AsyncSession, workflow_id: int, key: str) -> WorkflowState | None:
    """One entry, or ``None`` when the workflow never stored that key."""
    row = await session.execute(
        select(WorkflowState).where(
            WorkflowState.workflow_id == workflow_id, WorkflowState.key == key
        )
    )
    return row.scalar_one_or_none()


async def state_mapping(session: AsyncSession, workflow_id: int) -> dict[str, str]:
    """Every entry as a plain ``{key: value}`` dict, for placeholder rendering."""
    rows = await session.execute(
        select(WorkflowState.key, WorkflowState.value).where(
            WorkflowState.workflow_id == workflow_id
        )
    )
    return {key: value for key, value in rows.all()}


async def set_state(
    session: AsyncSession, workflow_id: int, payload: WorkflowStateWrite
) -> tuple[WorkflowState, bool]:
    """Upsert ``payload`` for ``workflow_id``; returns ``(row, created)``.

    Raises :class:`ValueError` when a *new* key would exceed
    :data:`WORKFLOW_STATE_MAX_KEYS`. Overwriting an existing key is always
    allowed, so a pipeline at the cap can still advance the keys it owns.
    """
    existing = await get_state(session, workflow_id, payload.key)
    if existing is not None:
        existing.value = payload.value
        await session.commit()
        await session.refresh(existing)
        return existing, False

    count = await session.scalar(
        select(func.count(WorkflowState.id)).where(WorkflowState.workflow_id == workflow_id)
    )
    if (count or 0) >= WORKFLOW_STATE_MAX_KEYS:
        raise ValueError(
            f"Workflow state is limited to {WORKFLOW_STATE_MAX_KEYS} keys; "
            "delete an unused key before adding a new one."
        )
    state = WorkflowState(workflow_id=workflow_id, key=payload.key, value=payload.value)
    session.add(state)
    await session.commit()
    await session.refresh(state)
    return state, True


async def delete_state(session: AsyncSession, workflow_id: int, key: str) -> bool:
    """Drop one entry. Returns ``False`` when the key wasn't there."""
    state = await get_state(session, workflow_id, key)
    if state is None:
        return False
    await session.delete(state)
    await session.commit()
    return True


async def clear_states(session: AsyncSession, workflow_id: int) -> int:
    """Drop every entry for a workflow (the UI's "reset"). Returns the count."""
    removed = await session.scalar(
        select(func.count(WorkflowState.id)).where(WorkflowState.workflow_id == workflow_id)
    )
    await session.execute(delete(WorkflowState).where(WorkflowState.workflow_id == workflow_id))
    await session.commit()
    return int(removed or 0)


async def build_state_index_prompt(session: AsyncSession, workflow_id: int) -> str | None:
    """Render the key index as a step-kickoff block, or ``None`` when empty.

    **Only keys** — never values, which can be large and are usually irrelevant
    to a given step. A step that wants one either names it in a
    ``{{state.<key>}}`` placeholder (resolved before the agent ever sees the
    text) or fetches it with ``workflow_state_get``.
    """
    entries = await list_state_keys(session, workflow_id)
    if not entries:
        return None
    lines = [_STATE_PROMPT_HEADER, ""]
    lines.extend(
        f"- `{e.key}` ({e.size} chars, updated {e.updated_at:%Y-%m-%d %H:%M} UTC)" for e in entries
    )
    return "\n".join(lines)


# -------------------------------------------------------------- placeholders


def render_placeholders(
    text: str,
    *,
    state: dict[str, str] | None = None,
    run_input: str | None = None,
    step_outputs: dict[int, str] | None = None,
) -> str:
    """Substitute ``{{…}}`` placeholders in a step's instructions.

    Supported expressions, each with an optional ``| default`` fallback used when
    the value is missing or empty:

    * ``{{state.<key>}}`` — a value from this workflow's saved state;
    * ``{{run.input}}`` — the brief the human gave when starting this run;
    * ``{{step.<n>.output}}`` — what the step at 0-based position ``n`` produced
      in this run.

    An expression we don't recognise is **left untouched**, so ordinary prose or
    another tool's templating that happens to use braces survives intact. A
    recognised expression that resolves to nothing becomes
    :data:`UNSET_PLACEHOLDER` — an honest "absent" the model can reason about,
    rather than a silent blank.
    """
    state = state or {}
    step_outputs = step_outputs or {}

    def resolve(match: re.Match[str]) -> str:
        expr = match.group("expr")
        default = match.group("default")
        value: str | None = None

        if expr.startswith("state."):
            value = state.get(expr[len("state.") :])
        elif expr == "run.input":
            value = run_input
        elif (step_match := _STEP_OUTPUT_RE.match(expr)) is not None:
            value = step_outputs.get(int(step_match.group("position")))
        else:
            return match.group(0)  # not ours — leave the text alone

        if value is not None and value.strip():
            return value.strip()
        if default is not None:
            return default.strip()
        return UNSET_PLACEHOLDER

    return _PLACEHOLDER_RE.sub(resolve, text)


def has_placeholders(text: str | None) -> bool:
    """Whether ``text`` contains at least one placeholder *we* would substitute."""
    if not text:
        return False
    return any(
        m.group("expr").startswith("state.")
        or m.group("expr") == "run.input"
        or _STEP_OUTPUT_RE.match(m.group("expr"))
        for m in _PLACEHOLDER_RE.finditer(text)
    )


async def step_outputs_for_run(session: AsyncSession, run_id: int) -> dict[int, str]:
    """What each position produced in ``run_id``, for ``{{step.N.output}}``.

    Reads the run trace rather than the agents' live artifacts, so a value stays
    resolvable after the blackboard is cleared, and a step re-driven by a gate
    loop-back resolves to its **latest** attempt (the trace is append-only, so
    the highest id for a position is the freshest).

    The trace holds a step's *whole* output, not the tighter summary shown on the
    agent list — the coordinator rebuilds it from the event archive when that
    display cap bit. It still has a ceiling of its own
    (``workflow.OUTPUT_SUMMARY_CAP``), but a value that hits it carries an
    explicit ``… [truncated: …]`` marker, so a step is never handed a severed
    payload that reads as a complete one.
    """
    rows = await session.execute(
        select(WorkflowRunStep.position, WorkflowRunStep.output_summary)
        .where(WorkflowRunStep.run_id == run_id)
        .order_by(WorkflowRunStep.id)
    )
    outputs: dict[int, str] = {}
    for position, summary in rows.all():
        if summary and summary.strip():
            outputs[position] = summary
    return outputs


async def render_step_instructions(
    session: AsyncSession, workflow_id: int, run_id: int | None, instructions: str
) -> str:
    """Resolve every placeholder in one step's instructions against live data."""
    run_input: str | None = None
    step_outputs: dict[int, str] = {}
    if run_id is not None:
        run = await session.get(WorkflowRun, run_id)
        run_input = run.input if run is not None else None
        step_outputs = await step_outputs_for_run(session, run_id)
    return render_placeholders(
        instructions,
        state=await state_mapping(session, workflow_id),
        run_input=run_input,
        step_outputs=step_outputs,
    )
