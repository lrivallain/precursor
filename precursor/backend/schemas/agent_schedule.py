"""Agent schedule schemas — recurrence config for scheduled agent sessions.

Mirrors the topic schedule schemas (``schemas/schedule.py``) and reuses its
recurrence guard-rails and the explicit-UTC datetime type. There is deliberately
no ``prompt`` field: a scheduled run replays the agent's own ``task_prompt``.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from precursor.backend.schemas.schedule import (
    ALL_DAYS_MASK,
    MAX_MINUTE_OF_DAY,
    MIN_INTERVAL_SECONDS,
    RuleListRead,
    RuleListWrite,
    RulesPayload,
    UtcDateTime,
)


class AgentScheduleSummary(BaseModel):
    """Compact schedule view embedded in ``AgentSessionRead``."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    enabled: bool
    # The primary rule, kept flat for backwards compatibility; `rules` carries
    # the complete set (primary first).
    interval_seconds: int
    days_of_week: int = ALL_DAYS_MASK
    run_at_minute: int | None = None
    timezone: str = "UTC"
    rules: RuleListRead
    clear_context: bool = True
    next_run_at: UtcDateTime | None = None
    last_run_at: UtcDateTime | None = None
    status: str = "idle"


class AgentScheduleRead(AgentScheduleSummary):
    id: int
    agent_session_id: int
    last_error: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class AgentScheduleCreate(RulesPayload):
    """Attach a recurrence to an existing agent session."""

    _cadence_required: ClassVar[bool] = True

    # Optional when `rules` is supplied; one of the two must be present.
    interval_seconds: int | None = Field(default=None, ge=MIN_INTERVAL_SECONDS)
    # At least one weekday must be selected (1) up to every day (127).
    days_of_week: int = Field(default=ALL_DAYS_MASK, ge=1, le=ALL_DAYS_MASK)
    # When set, recurrence is "daily at this minute-of-day" (in `timezone`)
    # and `interval_seconds` is ignored for cadence.
    run_at_minute: int | None = Field(default=None, ge=0, le=MAX_MINUTE_OF_DAY)
    timezone: str = Field(default="UTC", max_length=64)
    # Full rule set. Takes precedence over the flat fields above when non-empty.
    rules: RuleListWrite
    clear_context: bool = True
    enabled: bool = True


class AgentScheduleUpdate(RulesPayload):
    interval_seconds: int | None = Field(default=None, ge=MIN_INTERVAL_SECONDS)
    days_of_week: int | None = Field(default=None, ge=1, le=ALL_DAYS_MASK)
    # Pass an int to switch to / update daily-at-time mode; pass null to clear
    # it and return to interval mode. Use `model_fields_set` to distinguish
    # "omitted" from "explicit null" in the router.
    run_at_minute: int | None = Field(default=None, ge=0, le=MAX_MINUTE_OF_DAY)
    timezone: str | None = Field(default=None, max_length=64)
    # Replaces the whole rule set when supplied.
    rules: RuleListWrite
    clear_context: bool | None = None
    enabled: bool | None = None
