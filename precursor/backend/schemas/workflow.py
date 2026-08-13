"""Workflow schemas — the reusable agent-sequence orchestrator.

A workflow serialises as its ordered steps, each embedding a *compact* agent
summary (:class:`WorkflowAgentSummary`) rather than the full
``AgentSessionRead`` — the strip and step modal only need live status +
progress, and the full agent payload is fetched on demand when a step is opened.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from precursor.backend.schemas.agent import AgentApprovalPolicy, AgentPendingPermission
from precursor.backend.schemas.schedule import UtcDateTime

WorkflowStatus = Literal[
    "draft",
    "idle",
    "running",
    "paused",
    "awaiting_approval",
    "completed",
    "failed",
    "cancelled",
]

WorkflowStepKind = Literal["task", "inline", "gate", "approval"]

# What to do when a step's agent fails or its watchdog fires.
WorkflowStepErrorPolicy = Literal["fail", "retry", "continue"]

# What a human rejection at an approval checkpoint does next.
WorkflowStepRejectPolicy = Literal["rework", "stop", "skip"]

# How a step sources the context it is handed.
WorkflowStepContextMode = Literal["auto", "selected", "none"]


class WorkflowAgentSummary(BaseModel):
    """Just enough of a step's agent to render its node + drive controls."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    copilot_session_id: str | None = None
    title: str
    status: str
    # The agent's objective. Carried so an inline step (whose prompt lives with
    # the step, in a hidden vessel) can be edited from the workflow board.
    task_prompt: str = ""
    # True when this agent is a workflow step's private execution vessel.
    inline: bool = False
    progress: int | None = None
    progress_label: str | None = None
    result_summary: str | None = None
    active_narration: str | None = None
    # The question the agent raised when it parked itself (``status ==
    # "blocked"``). Carried so the board can show what it is stuck on and let the
    # operator answer it when resuming, instead of re-driving the step blind.
    blocked_question: str | None = None
    # The oldest unresolved tool-permission request parking this step, lifted
    # from the live runtime. Without it a gate on an *inline* step is
    # unanswerable: its vessel is hidden from the Agents roster, so the workflow
    # board is the only place the decision can be made.
    pending_permission: AgentPendingPermission | None = None
    finished_at: UtcDateTime | None = None
    updated_at: UtcDateTime


class WorkflowStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workflow_id: int
    position: int
    agent_id: int | None = None
    # Step behaviour: "task" (produce) or "gate" (PASS/FAIL check with loop-back).
    kind: WorkflowStepKind = "task"
    # For a gate: the position to re-drive on FAIL (null = previous step).
    on_fail_position: int | None = None
    # Per-run re-drive counter (surfaced so the strip can badge loop attempts).
    attempt_count: int = 0
    # Extra mandate applied to the agent for this step only.
    instructions: str | None = None
    # Failure handling for this step.
    on_error: WorkflowStepErrorPolicy = "fail"
    max_retries: int = 0
    retry_count: int = 0
    # For an approval step: what a rejection does next.
    on_reject: WorkflowStepRejectPolicy = "rework"
    # What this step inherits, and from which earlier steps.
    context_mode: WorkflowStepContextMode = "auto"
    context_sources: str | None = None
    # Capability overrides; null = inherit the agent's own setting.
    use_mcp: bool | None = None
    use_skills: bool | None = None
    use_memory: bool | None = None
    # Optional label override; falls back to the agent's title in the UI.
    name: str | None = None
    # Embedded live agent state (null when the referenced agent was deleted —
    # the builder renders this as a "missing agent" step to fix).
    agent: WorkflowAgentSummary | None = None


class WorkflowRunStepRead(BaseModel):
    """One step *attempt* within a run — the durable trace of what a step saw
    (``input_context``) and produced (``output_summary``)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    position: int
    kind: WorkflowStepKind = "task"
    label: str | None = None
    agent_id: int | None = None
    # True when that agent is private to its step (an inline prompt), so the UI
    # knows not to offer a link into the Agents section, where it isn't listed.
    agent_inline: bool = False
    attempt: int = 1
    status: str
    input_context: str | None = None
    output_summary: str | None = None
    gate_verdict: str | None = None
    # Token spend for this attempt (delta across the step's turn).
    input_tokens: int = 0
    output_tokens: int = 0
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_agent_inline(cls, data: Any) -> Any:
        """Lift the executing agent's ``inline`` flag onto the trace row.

        The ORM row carries the agent relationship, not the flag, and Pydantic
        won't reach through it on its own.
        """
        agent = getattr(data, "agent", None)
        if agent is not None:
            # ``from_attributes`` gives us the ORM object; return a dict so the
            # derived field lands alongside the mapped ones.
            return {
                **{f: getattr(data, f, None) for f in cls.model_fields if f != "agent_inline"},
                "agent_inline": bool(getattr(agent, "inline", False)),
            }
        return data


class WorkflowRunRead(BaseModel):
    """One execution of a workflow, with its ordered per-step attempt traces."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    workflow_id: int
    run_number: int
    status: str
    trigger: str
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    result_summary: str | None = None
    error: str | None = None
    # The per-run brief supplied at trigger time (null when the run was started
    # without one and executed on its steps' own objectives).
    input: str | None = None
    # Cumulative token spend across every attempt in this run.
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    step_runs: list[WorkflowRunStepRead] = []


class WorkflowSummary(BaseModel):
    """Just enough of a workflow to name and link it from another surface."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    icon: str | None = None
    status: WorkflowStatus


class WorkflowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    status: WorkflowStatus
    current_step_id: int | None = None
    current_run_id: int | None = None
    clear_artifacts: bool = True
    max_loops: int = 3
    # Stall watchdog: seconds a step may run before it's declared stuck (null =
    # no watchdog).
    step_timeout_seconds: int | None = None
    # Assistant Role applied to every step's agent while the workflow runs.
    role_id: int | None = None
    # Tool-approval policy applied to every step's agent while it runs; null
    # leaves each agent's own setting alone.
    approval_policy: AgentApprovalPolicy | None = None
    run_count: int = 0
    last_run_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    result_summary: str | None = None
    error: str | None = None
    # Scheduling
    schedule_enabled: bool = False
    interval_seconds: int | None = None
    run_at_minute: int | None = None
    timezone: str = "UTC"
    days_of_week: int = 127
    next_run_at: UtcDateTime | None = None
    # Presence-only: never echo the raw token in list payloads beyond what the
    # owner needs to copy it once. Kept simple here (single-user local app).
    webhook_token: str | None = None
    archived_at: UtcDateTime | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    steps: list[WorkflowStepRead] = []


# --- Requests --------------------------------------------------------------


class WorkflowStepInput(BaseModel):
    """One step in a create/replace payload. Either reference an existing agent
    by ``agent_id`` **or** author one here from ``task`` (+ optional title/
    model). An authored agent is plain and unattached — the workflow owns the
    chaining of it — and ``reusable`` decides whether it is listed in the Agents
    section or stays a private vessel owned by this step."""

    agent_id: int | None = None
    name: str | None = Field(default=None, max_length=200)
    # Step behaviour + gate/approval loop-back routing.
    kind: WorkflowStepKind = "task"
    on_fail_position: int | None = Field(default=None, ge=0)
    # Extra mandate layered on the agent's own objective, for this step only.
    instructions: str | None = Field(default=None, max_length=8000)
    # Failure handling for this step.
    on_error: WorkflowStepErrorPolicy = "fail"
    max_retries: int = Field(default=0, ge=0, le=10)
    # For an approval step: what a rejection does next.
    on_reject: WorkflowStepRejectPolicy = "rework"
    # What this step inherits, and from which earlier steps ("0,2").
    context_mode: WorkflowStepContextMode = "auto"
    context_sources: str | None = Field(default=None, max_length=200)
    # Capability overrides; null = inherit the agent's own setting.
    use_mcp: bool | None = None
    use_skills: bool | None = None
    use_memory: bool | None = None
    # Agent authored in the step (used when agent_id is null).
    task: str | None = None
    title: str | None = Field(default=None, max_length=200)
    model: str | None = None
    # Where the authored agent lives. ``False`` (the default, so an omitted flag
    # keeps the established behaviour) makes a private vessel: hidden from the
    # Agents section and deleted with the step. ``True`` mints a *reusable*
    # agent instead — listed, editable and outliving the step, exactly as if it
    # had been created in the Agents section and picked here.
    reusable: bool = False


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=24)
    clear_artifacts: bool = True
    max_loops: int = Field(default=3, ge=1, le=25)
    step_timeout_seconds: int | None = Field(default=None, ge=30, le=86400)
    role_id: int | None = None
    approval_policy: AgentApprovalPolicy | None = None
    steps: list[WorkflowStepInput] = []


class WorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=24)
    clear_artifacts: bool | None = None
    max_loops: int | None = Field(default=None, ge=1, le=25)
    # ``0`` disables the watchdog (mapped to null); omit to leave unchanged.
    step_timeout_seconds: int | None = Field(default=None, ge=0, le=86400)
    # ``0`` clears the role (mapped to null); omit to leave unchanged.
    role_id: int | None = Field(default=None, ge=0)
    # ``None`` is meaningful here (inherit each agent's own policy), so the
    # router keys off ``model_fields_set`` to tell it from "field omitted".
    approval_policy: AgentApprovalPolicy | None = None
    # When provided, replaces the entire ordered step list (add/remove/reorder
    # in one shot). Omit to leave steps untouched.
    steps: list[WorkflowStepInput] | None = None


class WorkflowScheduleUpdate(BaseModel):
    schedule_enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=60)
    run_at_minute: int | None = Field(default=None, ge=0, le=1439)
    timezone: str | None = None
    days_of_week: int | None = Field(default=None, ge=0, le=127)


class WorkflowRunRequest(BaseModel):
    """Optional body for starting a run: a per-run brief.

    Lets one reusable workflow ("analyse → review → report") be pointed at a
    different subject each time without editing its steps. Omit the body (or send
    ``input: null``) to run the pipeline autonomously as before.
    """

    input: str | None = Field(default=None, max_length=8000)


class WorkflowResumeRequest(BaseModel):
    """Optional body for resuming a paused workflow.

    A run pauses for two very different reasons. A **manual** pause just needs
    restarting, and sends no body. But a run also parks when its step's agent
    **blocks** on a question it can't answer alone — and re-driving that step
    unchanged would strand it on the same question. ``input`` is the answer:
    guidance injected into the resumed step's kickoff so it can get past what
    stopped it.
    """

    input: str | None = Field(default=None, max_length=8000)


class WorkflowPermissionDecision(BaseModel):
    """Answer the tool-permission gate parking a step.

    Distinct from ``WorkflowApprovalRequest``, which clears a human *approval
    step*. This one resolves a request the agent's runtime raised mid-step —
    the decision the board has to be able to make, because an inline step's
    agent is hidden from the Agents roster.
    """

    request_id: str = Field(min_length=1)
    decision: Literal["approve-once", "approve-always", "deny"]


class WorkflowRetryRequest(BaseModel):
    """Optional body for retrying a single step of a stopped run.

    ``position`` targets a specific step; omitted, the step whose failure stopped
    the run is retried. ``input`` is optional human guidance injected into the
    fresh attempt, for a failure the agent can't diagnose on its own.
    """

    position: int | None = Field(default=None, ge=0)
    input: str | None = Field(default=None, max_length=8000)


class WorkflowApprovalRequest(BaseModel):
    """Body for clearing or rejecting a human approval checkpoint.

    On approve, ``note`` is recorded on the trace as the reviewer's remark. On
    reject it's the feedback injected into the step being redone, so the agent
    knows what to fix.
    """

    note: str | None = Field(default=None, max_length=4000)
    # Reject only: override the step's ``on_reject`` policy for this decision.
    # Omit to use the checkpoint's declared intent.
    action: WorkflowStepRejectPolicy | None = None
