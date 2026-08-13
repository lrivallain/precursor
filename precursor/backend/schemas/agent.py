"""Agent session schemas — Agents mode (Copilot SDK) request/response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from precursor.backend.schemas.agent_schedule import AgentScheduleSummary
from precursor.backend.schemas.schedule import UtcDateTime

ContainerKind = Literal["topic", "chat"]
AgentApprovalPolicy = Literal["manual", "balanced", "autonomous"]
AgentStatus = Literal[
    "pending",
    "waiting",
    "running",
    "idle",
    "needs_approval",
    "blocked",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]


class AgentPendingPermission(BaseModel):
    """The oldest unresolved permission request blocking a live agent.

    Lets the dashboard surface an out-of-band "agent is waiting" signal that
    deep-links straight to the parked approval card (by ``request_id``).
    """

    request_id: str | None = None
    title: str | None = None


class AgentSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    copilot_session_id: str | None = None
    title: str
    task_prompt: str
    active_prompt: str | None = None
    status: AgentStatus
    result_summary: str | None = None
    error: str | None = None
    model: str | None = None
    # --- Autonomy / mission state (see model docstrings) ---
    autonomy_enabled: bool = False
    max_steps: int = 12
    step_count: int = 0
    progress: int | None = None
    progress_label: str | None = None
    blocked_question: str | None = None
    # Per-agent approval-policy override; null = inherit the global default.
    approval_policy: AgentApprovalPolicy | None = None
    # --- Fleet governance / budgets / retry (see model docstrings) ---
    token_budget: int | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    max_retries: int = 0
    retry_count: int = 0
    next_retry_at: UtcDateTime | None = None
    blueprint_id: int | None = None
    finished_at: UtcDateTime | None = None
    topic_id: int | None = None
    chat_id: int | None = None
    role_id: int | None = None
    # True when the agent is a workflow step's private execution vessel rather
    # than a reusable unit (hidden from the Agents roster).
    inline: bool = False
    # What this agent may draw on (all default on).
    use_mcp: bool = True
    use_skills: bool = True
    use_memory: bool = True
    last_activity_at: UtcDateTime | None = None
    archived_at: UtcDateTime | None = None
    last_read_at: UtcDateTime | None = None
    # Number of assistant replies produced since the user last opened the
    # session (computed server-side; mirrors ChatRead.unread_count).
    unread_count: int = 0
    # How many live (non-archived) workflows use this agent. Agents are shared,
    # so the list surfaces this to warn that an edit ripples into pipelines.
    workflow_count: int = 0
    created_at: UtcDateTime
    updated_at: UtcDateTime
    # Recurrence config + run state when the agent re-runs on a cadence (null
    # when unscheduled). Eager-loaded by the agents router.
    schedule: AgentScheduleSummary | None = None
    # Live in-flight activity, derived from the manager's in-memory event cache
    # (null/0 when the session isn't running in-process). These power the
    # dashboard cockpit's "what is it doing right now" indicators and are not
    # persisted — they reflect the current turn only.
    active_tool: str | None = None
    # Distinct tool calls currently running for this agent — the "sub-agent
    # fan-out" cluster indicator (>1 means parallel work in flight).
    active_tool_count: int = 0
    # A one-line, plain-language hint of what the agent is doing right now,
    # distilled from its own in-flight commentary (null when idle / not live).
    active_narration: str | None = None
    pending_permission: AgentPendingPermission | None = None
    # --- Orchestration relations (eager-loaded by the router) ---
    # External webhook triggers registered on this agent.
    triggers: list[AgentTriggerRead] = []
    # Published blackboard outputs, newest first.
    artifacts: list[AgentArtifactRead] = []


class AgentTriggerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int
    type: str
    token: str
    enabled: bool
    last_fired_at: UtcDateTime | None = None
    created_at: UtcDateTime


class AgentArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int
    key: str | None = None
    kind: str
    title: str
    content: str
    created_at: UtcDateTime
    updated_at: UtcDateTime


class AgentSessionCreate(BaseModel):
    """Start a new agent task. ``task`` is the initial instruction/objective."""

    task: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=200)
    model: str | None = None
    topic_id: int | None = None
    chat_id: int | None = None
    role_id: int | None = None
    # Opt the session into the autonomous goal loop (default off). ``max_steps``
    # bounds how many continuation steps it may take before pausing for a human.
    autonomy_enabled: bool = False
    max_steps: int = Field(default=12, ge=1, le=100)
    # Per-agent approval-policy override; null = inherit the global default.
    approval_policy: AgentApprovalPolicy | None = None
    # --- Fleet governance / retry (all optional; null = unset/ungoverned) ---
    token_budget: int | None = Field(default=None, ge=1)
    max_retries: int = Field(default=0, ge=0, le=10)
    # Stamp the new agent from this blueprint (fields below still override).
    blueprint_id: int | None = None
    # Whether to launch immediately. ``False`` parks the agent in the ``waiting``
    # state until a trigger fires (a parent completing, a webhook, or a manual
    # "Start now") — letting you wire a fleet up front and arm it later.
    start: bool = True


class AgentSendRequest(BaseModel):
    """Send a follow-up message into a running/idle agent session."""

    message: str = Field(min_length=1)


class AgentUpdateRequest(BaseModel):
    """Rename an agent session and/or edit its task instructions.

    Both fields are optional so a caller can patch either independently (the
    Settings drawer sends ``title`` on rename and ``task`` when the instructions
    are edited). ``role_id`` reassigns the agent's Assistant Role.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    task: str | None = Field(default=None, min_length=1)
    role_id: int | None = None
    # Capability toggles — what the agent may draw on. Omit to leave unchanged.
    use_mcp: bool | None = None
    use_skills: bool | None = None
    use_memory: bool | None = None
    # Toggle autonomy or retune the step budget after creation. Both optional so
    # the Settings drawer can patch either independently.
    autonomy_enabled: bool | None = None
    max_steps: int | None = Field(default=None, ge=1, le=100)
    # Change the per-agent approval policy. ``None`` is a *meaningful* value here
    # (inherit the global default), so the router keys off ``model_fields_set``
    # to tell "reset to inherit" apart from "leave unchanged".
    approval_policy: AgentApprovalPolicy | None = None
    # Retune governance after creation. ``token_budget=None`` is meaningful
    # (ungovern), so the router keys off ``model_fields_set``.
    token_budget: int | None = Field(default=None, ge=1)
    max_retries: int | None = Field(default=None, ge=0, le=10)


class AgentLinkRequest(BaseModel):
    """Attach or detach the session to a container. Both null = detach."""

    topic_id: int | None = None
    chat_id: int | None = None


class AgentPermissionDecision(BaseModel):
    """Resolve a pending permission request for an agent session."""

    request_id: str
    decision: Literal["approve-once", "approve-always", "deny"]


class AgentEvent(BaseModel):
    """A normalised event from the SDK session, shaped for the workflow UI.

    ``kind`` drives the step renderer (a tool call, reasoning, an assistant
    message, a permission request, etc.). The raw payload is preserved under
    ``data`` for renderers that want more detail.
    """

    kind: str
    text: str | None = None
    tool_name: str | None = None
    tool_status: str | None = None  # running | done | error
    request_id: str | None = None
    data: dict[str, Any] | None = None
    at: UtcDateTime | None = None


class AgentModelInfo(BaseModel):
    """A model available to the agents runtime (for the default-model picker)."""

    id: str
    name: str
    # Max input tokens the model accepts (``None`` when the runtime omits it).
    context_window: int | None = None
    # Reasoning-effort values this model accepts (empty when not reasoning-capable).
    supported_reasoning_efforts: list[str] = []


class AgentPermissionGrant(BaseModel):
    """An active "approve for session" grant, for the Settings security recap."""

    agent_id: int
    type: str
    title: str | None = None
    target: str | None = None
    at: UtcDateTime | None = None


# --- Blueprints (reusable agent templates) ---------------------------------
class AgentBlueprintRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    task_prompt: str
    model: str | None = None
    role_id: int | None = None
    approval_policy: AgentApprovalPolicy | None = None
    autonomy_enabled: bool = False
    max_steps: int = 12
    token_budget: int | None = None
    max_retries: int = 0
    icon: str | None = None
    color: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class AgentBlueprintCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    task_prompt: str = Field(default="", max_length=20000)
    model: str | None = None
    role_id: int | None = None
    approval_policy: AgentApprovalPolicy | None = None
    autonomy_enabled: bool = False
    max_steps: int = Field(default=12, ge=1, le=100)
    token_budget: int | None = Field(default=None, ge=1)
    max_retries: int = Field(default=0, ge=0, le=10)
    icon: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=24)


class AgentBlueprintUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    task_prompt: str | None = Field(default=None, max_length=20000)
    model: str | None = None
    role_id: int | None = None
    approval_policy: AgentApprovalPolicy | None = None
    autonomy_enabled: bool | None = None
    max_steps: int | None = Field(default=None, ge=1, le=100)
    token_budget: int | None = Field(default=None, ge=1)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    icon: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=24)


class AgentBlueprintInstantiate(BaseModel):
    """Spawn an agent from a blueprint. Optional overrides on top of it."""

    title: str | None = Field(default=None, max_length=200)
    task: str | None = Field(default=None, min_length=1)
    topic_id: int | None = None
    chat_id: int | None = None
    # Start it immediately (default) or leave it pending for manual start.
    start: bool = True


# --- Triggers / artifacts write models --------------------------------------


class AgentTriggerCreate(BaseModel):
    type: Literal["webhook"] = "webhook"
    enabled: bool = True


class AgentArtifactCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=100000)
    kind: Literal["text", "markdown", "json", "link"] = "text"
    key: str | None = Field(default=None, max_length=80)


# --- Unified inbox + aggregate observability --------------------------------
class AgentInboxItem(BaseModel):
    """One thing waiting on a human: a raised question or a permission gate."""

    agent_id: int
    title: str
    # "blocked" (agent raised a question), "needs_approval" (tool gate), or
    # "budget" (parked because it hit its token budget).
    kind: Literal["blocked", "needs_approval", "budget"]
    detail: str | None = None
    request_id: str | None = None
    at: UtcDateTime | None = None


class AgentStatusCount(BaseModel):
    status: AgentStatus
    count: int


class AgentMetrics(BaseModel):
    """Fleet-wide rollup for the dashboard header."""

    total: int
    active: int  # running + needs_approval
    waiting: int  # blocked + parked on budget (in the inbox)
    completed: int
    failed: int
    by_status: list[AgentStatusCount] = []
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    # Agents currently executing a turn out of the concurrency budget.
    running_now: int = 0
    max_concurrent: int = 0


# Resolve forward refs used by AgentSessionRead (sub-models defined below it).
AgentSessionRead.model_rebuild()
