"""Schemas for tool approvals (see ``services/approvals.py``)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from precursor.backend.services.approval_rules import (
    ARG_OPS,
    MAX_CLAUSES,
    OP_UNDER,
    normalize_path,
    parse_clauses,
)

ApprovalPolicy = Literal["manual", "balanced", "autonomous"]


class ArgClause(BaseModel):
    """One condition on a tool call's arguments (all clauses of a rule must hold)."""

    arg: str = Field(min_length=1, max_length=64)
    op: Literal["equals", "glob", "under"]
    value: str = Field(min_length=1, max_length=512)

    @field_validator("arg")
    @classmethod
    def _arg_name(cls, v: str) -> str:
        v = v.strip()
        if not re.fullmatch(r"[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)*", v):
            raise ValueError("arg must be an argument name, or dotted names for nested ones")
        return v

    @model_validator(mode="after")
    def _under_value(self) -> Self:
        if self.op == OP_UNDER:
            base = normalize_path(self.value)
            if base in ("", ".") or base == ".." or base.startswith("../"):
                raise ValueError("'under' needs a folder path that stays inside its root")
            self.value = base
        return self


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
    scope: Literal["once", "conversation", "global"]
    feedback: str | None = None
    executed: bool
    created_at: datetime | None = None
    decided_at: datetime | None = None


class ToolApprovalDecision(BaseModel):
    """The user's answer to one pending approval."""

    approve: bool
    # ``conversation`` / ``global`` also save an allow rule for this tool, so later
    # calls skip the card (in this conversation, or everywhere).
    scope: Literal["once", "conversation", "global"] = "once"
    # Narrows the saved rule to calls whose arguments match; ignored for ``once``.
    arg_match: list[ArgClause] = Field(default_factory=list)
    feedback: str | None = None


class ApprovalDecisionResult(BaseModel):
    approval: ToolApprovalRead
    # Approvals still waiting after this decision; resume the turn when it hits 0.
    pending: int


RuleEffect = Literal["allow", "ask"]
RuleScope = Literal["global", "topic", "chat"]


def _validate_clauses(clauses: list[ArgClause]) -> list[ArgClause]:
    if len(clauses) > MAX_CLAUSES:
        raise ValueError(f"A rule takes at most {MAX_CLAUSES} argument conditions")
    return clauses


class ApprovalRuleCreate(BaseModel):
    effect: RuleEffect
    # Globs: ``*`` matches anything, so ``server="*", tool="*"`` is every tool.
    server: str = Field(default="*", min_length=1, max_length=128)
    tool: str = Field(default="*", min_length=1, max_length=255)
    arg_match: list[ArgClause] = Field(default_factory=list)
    scope: RuleScope = "global"
    topic_id: int | None = None
    chat_id: int | None = None
    note: str | None = Field(default=None, max_length=255)

    @field_validator("arg_match")
    @classmethod
    def _clauses(cls, v: list[ArgClause]) -> list[ArgClause]:
        return _validate_clauses(v)

    @model_validator(mode="after")
    def _owner(self) -> Self:
        want_topic = self.scope == "topic"
        want_chat = self.scope == "chat"
        if (self.topic_id is not None) != want_topic or (self.chat_id is not None) != want_chat:
            raise ValueError("scope 'topic' needs topic_id, 'chat' needs chat_id, 'global' neither")
        return self


class ApprovalRuleUpdate(BaseModel):
    """The editable parts of a rule; scope and owner are fixed at creation."""

    effect: RuleEffect | None = None
    server: str | None = Field(default=None, min_length=1, max_length=128)
    tool: str | None = Field(default=None, min_length=1, max_length=255)
    arg_match: list[ArgClause] | None = None
    enabled: bool | None = None
    note: str | None = Field(default=None, max_length=255)

    @field_validator("arg_match")
    @classmethod
    def _clauses(cls, v: list[ArgClause] | None) -> list[ArgClause] | None:
        return None if v is None else _validate_clauses(v)


class ApprovalRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    effect: RuleEffect
    server: str
    tool: str
    arg_match: list[ArgClause]
    scope: RuleScope
    topic_id: int | None = None
    chat_id: int | None = None
    enabled: bool
    note: str | None = None
    hit_count: int
    last_used_at: datetime | None = None
    created_from_approval_id: int | None = None
    created_at: datetime | None = None

    @field_validator("arg_match", mode="before")
    @classmethod
    def _from_json(cls, v: object) -> object:
        if isinstance(v, str):
            return [
                {"arg": c.arg, "op": c.op, "value": c.value}
                for c in parse_clauses(v)
                if c.op in ARG_OPS
            ]
        return v
