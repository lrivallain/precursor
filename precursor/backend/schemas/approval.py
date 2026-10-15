"""Schemas for tool approvals (see ``services/approvals.py``)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ApprovalPolicy = Literal["manual", "balanced", "autonomous"]


class ToolApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    topic_id: int | None = None
    chat_id: int | None = None
    assistant_message_id: int | None = None
    tool_call_id: str
    tool_name: str
    server: str
    raw_tool: str
    arguments: str
    kind: Literal["tool", "intent"]
    reason: str | None = None
    status: Literal["pending", "approved", "denied", "cancelled"]
    scope: Literal["once", "conversation"]
    feedback: str | None = None
    executed: bool
    created_at: datetime | None = None
    decided_at: datetime | None = None


class ToolApprovalDecision(BaseModel):
    """The user's answer to one pending approval."""

    approve: bool
    # ``conversation`` also allows later calls to the same tool in this conversation.
    scope: Literal["once", "conversation"] = "once"
    feedback: str | None = None


class ApprovalDecisionResult(BaseModel):
    approval: ToolApprovalRead
    # Approvals still waiting after this decision; resume the turn when it hits 0.
    pending: int
