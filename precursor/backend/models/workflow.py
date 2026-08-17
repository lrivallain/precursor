"""Workflow models — a reusable, coordinated sequence of independent agents.

Where a bare :class:`~precursor.backend.models.agent_session.AgentSession` runs a
single objective in isolation, a **Workflow** lifts coordination into a
first-class, reusable object: the workflow owns an ordered list of steps, each
step pointing at an otherwise-independent
:class:`~precursor.backend.models.agent_session.AgentSession`. Running the
workflow runs step 1's agent, and when that agent rests the coordinator advances
to step 2 (injecting the prior step's artifacts), and so on. The same workflow can
be re-run, paused, cancelled, scheduled, or fired by webhook — the *workflow* is
the coordinator, so an agent stays a plain, reusable unit that any number of
workflows may reference.

This is the "workflow engine" model (n8n / Temporal / Prefect-style): a durable
definition (``Workflow`` + ``WorkflowStep``) plus live run state cached on the
row (``status``, ``current_step_id``, ``run_count``) so the Workflows tab can list
and drive pipelines without booting the runtime.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.agent_session import AgentSession
    from precursor.backend.models.workflow_state import WorkflowState


# Lifecycle of a workflow. ``draft`` = created but never run (or has no steps);
# ``idle`` = ready, previously run and at rest; ``running`` = a step's agent is
# active or being advanced; ``paused`` = held between steps by the operator;
# terminal states mirror the agent's.
WORKFLOW_STATUSES = (
    "draft",
    "idle",
    "running",
    "paused",
    "awaiting_approval",
    "completed",
    "failed",
    "cancelled",
)

# What a step *is*. ``task`` produces via a reusable agent you picked; ``inline``
# produces via a one-off prompt owned by the step (its agent is hidden and dies
# with it); ``gate`` judges (an agent votes PASS/FAIL); ``approval`` parks the run
# for a **human** decision — no agent runs for it.
WORKFLOW_STEP_KINDS = ("task", "inline", "gate", "approval")

# Step kinds that produce content the pipeline hands downstream. Gates emit only
# a verdict and approvals emit nothing, so both are transparent to the data flow.
WORKFLOW_PRODUCING_KINDS = ("task", "inline")

# What to do when a step's agent fails (or its watchdog times it out).
# ``fail`` (default) stops the run — today's behaviour. ``retry`` re-drives the
# same step up to ``WorkflowStep.max_retries`` times before falling back to
# stopping. ``continue`` records the failure and carries on to the next step,
# for steps whose output is a nice-to-have (an optional enrichment, a notify).
WORKFLOW_STEP_ERROR_POLICIES = ("fail", "retry", "continue")

# How a step sources the context it is handed.
WORKFLOW_STEP_CONTEXT_MODES = ("auto", "selected", "none")

# What a rejection at a human ``approval`` checkpoint does next. ``rework``
# (default) sends the work back to an earlier step to be redone — the reviewer
# wants a better version. ``stop`` ends the run there: the human's answer is
# "no, don't do this at all", which is the whole point of a checkpoint in front
# of an irreversible action. ``skip`` abandons only the rejected work and lets
# the rest of the pipeline carry on.
WORKFLOW_STEP_REJECT_POLICIES = ("rework", "stop", "skip")


def _mint_token() -> str:
    return secrets.token_urlsafe(24)


class Workflow(Base, TimestampMixin):
    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Portable identity for YAML export/import — see ``AgentSession.export_id``.
    # Lets a re-imported file update the workflow it came from rather than
    # matching it by name. Nullable for legacy rows; minted on first export.
    export_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False, default="Workflow")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Presentation for the gallery card (a lucide icon name + a section tint key).
    icon: Mapped[str | None] = mapped_column(String(40), nullable=True)
    color: Mapped[str | None] = mapped_column(String(24), nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft", index=True
    )

    # The step whose agent is currently active while ``status == "running"`` (or
    # the step the run is paused *before*). Null when idle/draft/terminal. SET
    # NULL keeps the workflow row valid if the step is deleted mid-flight.
    current_step_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_steps.id", ondelete="SET NULL"), nullable=True
    )

    # The run currently in flight (or the most recently finished one). Points the
    # coordinator at the ``WorkflowRun`` whose per-step traces it is appending to,
    # so a run-trace survives independently of the churny ``current_step_id``. SET
    # NULL if the run row is ever pruned.
    current_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True
    )

    # When true (default), each run wipes every referenced agent's prior
    # artifacts before its step runs, so a repeated pipeline produces fresh
    # outputs instead of stacking runs.
    clear_artifacts: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )

    # Safety cap on conditional loop-backs (see WorkflowStep.kind == "gate"): a
    # gate that keeps voting FAIL will re-drive its target at most this many times
    # per run before the workflow gives up (``failed``). Prevents runaway loops.
    max_loops: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")

    # Stall watchdog: how long a single step may stay ``running`` before the
    # coordinator declares it stuck, cancels its agent and applies the step's
    # ``on_error`` policy. Null = no watchdog (a hung agent parks the run
    # indefinitely, the pre-watchdog behaviour). Guards unattended pipelines
    # against an agent that never returns.
    step_timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Assistant Role applied to every step's agent for the duration of a run, so
    # a pipeline speaks with one voice without stamping the persona onto each
    # (shared, reusable) agent row. Null leaves each agent's own role alone.
    role_id: Mapped[int | None] = mapped_column(
        ForeignKey("roles.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Tool-approval policy applied to every step's agent while the workflow runs
    # ("manual" / "balanced" / "autonomous"). Null leaves each agent's own
    # setting alone. This is the lever that makes an *unattended* pipeline
    # actually unattended: a step that stops at a permission gate parks the whole
    # run until a human answers, which defeats a scheduled or webhook-fired
    # workflow. Set once here rather than on every shared agent, which would
    # change how those agents behave outside this pipeline too.
    approval_policy: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Run bookkeeping for the gallery + metrics.
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Short human-facing outcome (e.g. the final step's summary).
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Scheduling (recurrence) --------------------------------------------
    # Mirrors AgentSchedule but inlined on the workflow row: the background
    # scheduler polls ``schedule_enabled`` workflows whose ``next_run_at`` is due
    # and starts a fresh run. A null ``interval_seconds`` with a ``run_at_minute``
    # is daily-at-time; otherwise interval mode.
    schedule_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_at_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC"
    )
    days_of_week: Mapped[int] = mapped_column(
        Integer, nullable=False, default=127, server_default="127"
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # --- Webhook trigger -----------------------------------------------------
    # When set, POST /api/workflows/hooks/{token} starts a run. Minted on demand
    # (null = no webhook). Unique so a token resolves to exactly one workflow.
    webhook_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )

    # Non-null once archived (hidden from the active gallery, kept for history).
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Ordered steps. Eager (selectin) so the API serialises the pipeline in one
    # go; ordered by position so the sequence renders left-to-right.
    steps: Mapped[list[WorkflowStep]] = relationship(
        "WorkflowStep",
        back_populates="workflow",
        cascade="all, delete-orphan",
        order_by="WorkflowStep.position",
        foreign_keys="WorkflowStep.workflow_id",
        lazy="selectin",
    )

    # Persisted run history (newest first). Each run captures its per-step traces
    # — the input each step received, the output it produced, gate verdicts and
    # timing — so the Workflows page can trace and compare previous runs even
    # after ``clear_artifacts`` wipes the live agents' artifacts for the next run.
    runs: Mapped[list[WorkflowRun]] = relationship(
        "WorkflowRun",
        back_populates="workflow",
        cascade="all, delete-orphan",
        order_by="WorkflowRun.run_number.desc()",
        foreign_keys="WorkflowRun.workflow_id",
        lazy="noload",
    )

    # The pipeline's own durable memory: named values that outlive a run, written
    # by one step and read by another (or by the next run). Not eager-loaded —
    # values can be sizeable and nothing in the workflow serialisation needs
    # them, so they're fetched only through the state API. The cascade still
    # resolves on delete because ``AsyncSession.delete`` is awaited (it loads
    # unloaded cascades), which keeps cleanup correct on SQLite, where
    # ``ON DELETE CASCADE`` is inert with foreign keys off.
    states: Mapped[list[WorkflowState]] = relationship(
        "WorkflowState",
        back_populates="workflow",
        cascade="all, delete-orphan",
        order_by="WorkflowState.key",
        foreign_keys="WorkflowState.workflow_id",
    )

    @staticmethod
    def mint_webhook_token() -> str:
        return _mint_token()


class WorkflowStep(Base, TimestampMixin):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint("workflow_id", "position", name="uq_workflow_step_position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The agent this step runs. SET NULL (not CASCADE) so deleting a shared agent
    # leaves a visible "missing agent" step rather than silently reshaping the
    # pipeline — the builder flags it for the operator to fix.
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Zero-based order within the workflow. Unique per workflow (see table args).
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Step behaviour. ``task`` (default) is a normal producing step. ``gate`` is
    # a checker: its agent reviews the prior output and votes by ending its turn
    # with ``OBJECTIVE_COMPLETE: PASS: …`` or ``OBJECTIVE_COMPLETE: FAIL: …``. A
    # FAIL loops the run back to ``on_fail_position`` (default: the previous
    # step) with the failure reason injected, until the gate passes or the
    # workflow's ``max_loops`` cap is hit.
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="task", server_default="task"
    )
    # For a gate: the zero-based position to re-drive on a FAIL verdict. Null =
    # the immediately preceding runnable step. Position-based (not an FK) because
    # the builder replaces the step list wholesale on every save, churning ids.
    on_fail_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Per-run counter of how many times this step has been (re)driven by a gate
    # loop-back. Reset to 0 for every step at the start of each run.
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # Extra mandate for *this* step, appended to the agent's own objective for
    # this run only. What makes an agent genuinely reusable: one "Summariser" row
    # can be the terse-bullets step in one workflow and the exec-brief step in
    # another, without cloning the agent. Supports ``{{run.input}}``,
    # ``{{step.N.output}}`` and ``{{state.<key>}}`` placeholders, each with an
    # optional ``| default`` (see ``services/workflow_state.render_placeholders``).
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Failure handling ---------------------------------------------------
    # What to do when this step's agent fails or stalls. See
    # ``WORKFLOW_STEP_ERROR_POLICIES``.
    on_error: Mapped[str] = mapped_column(
        String(16), nullable=False, default="fail", server_default="fail"
    )
    # For ``on_error="retry"``: how many times to re-drive this step before
    # giving up and stopping the run. 0 with "retry" behaves like "fail".
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Per-run counter of *failure* retries taken (distinct from ``attempt_count``,
    # which counts gate loop-backs). Reset for every step at run start.
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # For an ``approval`` step: what a human rejection does next. See
    # ``WORKFLOW_STEP_REJECT_POLICIES``. The reviewer can override it per
    # decision, but this is the checkpoint's declared intent.
    on_reject: Mapped[str] = mapped_column(
        String(16), nullable=False, default="rework", server_default="rework"
    )

    # --- What this step is fed ----------------------------------------------
    # ``auto`` (default) is the implicit pipeline hand-off: the previous
    # producer's output plus the accumulated artifact blackboard. ``selected``
    # narrows it to specific earlier steps (see ``context_sources``) — the lever
    # for long pipelines, where inheriting everything is both expensive and
    # distracting. ``none`` runs the step on its own objective alone.
    context_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="auto", server_default="auto"
    )
    # For ``context_mode="selected"``: comma-separated 0-based step positions to
    # inherit from, oldest first (e.g. "0,2"). Position-based (not ids) to match
    # ``on_fail_position`` — the builder replaces the step list wholesale on save.
    context_sources: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Per-step capability overrides. Null = inherit the agent's own setting.
    use_mcp: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    use_skills: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    use_memory: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Which MCP servers this step may see, comma-separated by name. Tools are
    # the dominant cost in a step's context — a whole catalogue can be an order
    # of magnitude more input tokens than the prompt — and a step that can see a
    # tool eventually reaches for it, whatever the instructions say. Tri-state,
    # like the toggles above: null = every enabled server (today's behaviour), a
    # list = only those, and an explicit empty string = none at all, which is
    # exactly ``use_mcp=False``.
    mcp_servers: Mapped[str | None] = mapped_column(String(400), nullable=True)

    # Optional per-step display label overriding the agent's title in the strip.
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    workflow: Mapped[Workflow] = relationship(
        "Workflow", back_populates="steps", foreign_keys=[workflow_id]
    )
    agent: Mapped[AgentSession | None] = relationship("AgentSession")


# --- Run history -----------------------------------------------------------
# A run-trace is a durable, append-only record of one execution of a workflow.
# The live ``Workflow`` row caches only the *latest* outcome; these tables keep
# the full trail so the UI can browse and compare previous runs, and — crucially
# — show what input each step received and what it produced, even after the next
# run wipes the agents' live artifacts.

# Lifecycle of a single run. ``running`` while a step is active; terminal states
# mirror the workflow's; ``paused`` when the run is held between steps.
WORKFLOW_RUN_STATUSES = (
    "running",
    "paused",
    "awaiting_approval",
    "completed",
    "failed",
    "cancelled",
)

# What kicked the run off (surfaced as a badge on the run in the trace UI).
WORKFLOW_RUN_TRIGGERS = ("manual", "schedule", "webhook", "resume")


class WorkflowRun(Base, TimestampMixin):
    __tablename__ = "workflow_runs"
    __table_args__ = (UniqueConstraint("workflow_id", "run_number", name="uq_workflow_run_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 1-based run ordinal (mirrors the workflow's ``run_count`` at start time), so
    # the trace UI can label runs "Run #3" stably and order them.
    run_number: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="running", server_default="running", index=True
    )
    trigger: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", server_default="manual"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional per-run brief supplied by whoever triggered it: the file to
    # analyse, the topic to research, the payload a webhook posted. The workflow
    # definition stays generic and reusable; this is the run's *subject*. Null
    # means "no brief" — the pipeline runs on its steps' own objectives alone.
    # Injected into every step's kickoff preamble so the whole run shares intent.
    input: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Cumulative token spend across every step attempt in this run, rolled up as
    # each step finalizes. Turns "did it work?" into "was it worth it?" — and
    # makes the cost of a long pipeline (or a gate looping five times) visible.
    total_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    workflow: Mapped[Workflow] = relationship(
        "Workflow", back_populates="runs", foreign_keys=[workflow_id]
    )
    # Per-step traces in execution order (append-only within a run — a gate
    # loop-back re-runs a step and appends a fresh trace row, so attempts show as
    # distinct entries).
    step_runs: Mapped[list[WorkflowRunStep]] = relationship(
        "WorkflowRunStep",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="WorkflowRunStep.id",
        lazy="selectin",
    )


class WorkflowRunStep(Base, TimestampMixin):
    __tablename__ = "workflow_run_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Position + kind + label snapshot the step *as it was* when the run executed,
    # so a later edit to the workflow definition doesn't rewrite history.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="task", server_default="task"
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="Step")
    # The agent that executed this trace (SET NULL if later deleted — the trace
    # keeps its input/output snapshot regardless).
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="SET NULL"), nullable=True
    )
    # The specific *execution* of that agent. This is what makes two workflows
    # able to drive the same agent at once: the coordinator resolves the turn by
    # run, never by agent. Nullable for traces recorded before runs existed.
    agent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 1-based attempt within this run (a gate loop-back re-drives a step, bumping
    # this so repeated attempts are legible in the trace).
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    # True when this attempt was a **manual replay**: an operator re-ran one step
    # on its own, out of band, feeding it the exact input a previous attempt saw.
    # It is deliberately *not* a pipeline attempt — nothing advances when it ends
    # — so every coordinator lookup that means "the turn the run is waiting on"
    # filters these out, and the trace badges them so a run's history still reads
    # as the pipeline that actually executed.
    replay: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="running", server_default="running"
    )
    # The kickoff preamble the step received (previous output + blackboard +
    # gate/fail instructions) — the "input" the trace UI shows. Uncapped Text.
    input_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The deliverable the step produced (its result summary / full body).
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # For a gate trace: "PASS" or "FAIL".
    gate_verdict: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # Token spend for *this attempt*, computed as the delta between the run's
    # counters at launch and at finalize. Stored per attempt so a gate that
    # looped four times shows what each pass actually cost.
    input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Internal: the run's counters when this attempt started, so the delta above
    # can be computed at finalize. Normally 0 (a fresh run starts empty), but a
    # permission gate can reopen a trace mid-run, and a run's counters keep
    # accumulating across the reopen. Not surfaced in the API.
    token_baseline_in: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    token_baseline_out: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[WorkflowRun] = relationship(
        "WorkflowRun", back_populates="step_runs", foreign_keys=[run_id]
    )
    # Eager-loaded so the trace can tell whether this attempt's agent is a real,
    # reusable one (worth a link into the Agents cockpit) or a step-private
    # vessel that isn't listed there at all.
    agent: Mapped[AgentSession | None] = relationship("AgentSession", lazy="selectin")
