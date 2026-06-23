"""Agent session schemas — Agents mode (Copilot SDK) request/response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from precursor.backend.schemas.schedule import UtcDateTime

ContainerKind = Literal["topic", "chat"]
AgentStatus = Literal[
    "pending",
    "running",
    "idle",
    "needs_approval",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]


class AgentSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    copilot_session_id: str | None = None
    title: str
    task_prompt: str
    active_prompt: str | None = None
    streaming: bool = False
    status: AgentStatus
    result_summary: str | None = None
    error: str | None = None
    model: str | None = None
    topic_id: int | None = None
    chat_id: int | None = None
    last_activity_at: UtcDateTime | None = None
    archived_at: UtcDateTime | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class AgentSessionCreate(BaseModel):
    """Start a new agent task. ``task`` is the initial instruction."""

    task: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=200)
    model: str | None = None
    streaming: bool = False
    topic_id: int | None = None
    chat_id: int | None = None


class AgentSendRequest(BaseModel):
    """Send a follow-up message into a running/idle agent session."""

    message: str = Field(min_length=1)
    # Optional per-turn streaming override. When set and different from the
    # session's current mode, the live session is recreated so the next turn
    # streams (or stops streaming).
    streaming: bool | None = None


class AgentUpdateRequest(BaseModel):
    """Rename an agent session."""

    title: str = Field(min_length=1, max_length=200)


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


class AgentPermissionGrant(BaseModel):
    """An active "approve for session" grant, for the Settings security recap."""

    agent_id: int
    type: str
    title: str | None = None
    target: str | None = None
    at: UtcDateTime | None = None
