"""Schedule schemas — recurrence config for scheduled topics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer


def _as_utc_iso(value: datetime) -> str:
    """Serialize a datetime as an explicit-UTC ISO string.

    Schedule timestamps are stored in UTC, but SQLite drops the tzinfo on read,
    leaving naive datetimes that serialize without an offset. The browser then
    parses ``2026-06-15T05:45:00`` as *local* time, shifting the displayed
    "next run" by the local UTC offset. Stamping ``+00:00`` (assuming naive ==
    UTC) makes clients render the correct local time.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


# A datetime that always serializes with an explicit UTC offset.
UtcDateTime = Annotated[datetime, PlainSerializer(_as_utc_iso, return_type=str)]

# Guard rail: never run more often than once a minute.
MIN_INTERVAL_SECONDS = 60
# 7-bit weekday mask (bit 0 = Monday … bit 6 = Sunday). 127 = every day.
ALL_DAYS_MASK = 127
# run_at_minute bounds: minutes since local midnight.
MAX_MINUTE_OF_DAY = 24 * 60 - 1


class ScheduleSummary(BaseModel):
    """Compact schedule view embedded in the sidebar TopicNode."""

    model_config = ConfigDict(from_attributes=True)

    enabled: bool
    interval_seconds: int
    days_of_week: int = ALL_DAYS_MASK
    run_at_minute: int | None = None
    timezone: str = "UTC"
    clear_context: bool = False
    next_run_at: UtcDateTime | None = None
    last_run_at: UtcDateTime | None = None
    status: str = "idle"


class ScheduleRead(ScheduleSummary):
    id: int
    topic_id: int
    prompt: str
    last_error: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class ScheduleCreate(BaseModel):
    """Create a scheduled topic + its recurrence in one call."""

    title: str = Field(min_length=1, max_length=255)
    prompt: str = Field(min_length=1)
    interval_seconds: int = Field(ge=MIN_INTERVAL_SECONDS)
    # At least one weekday must be selected (1) up to every day (127).
    days_of_week: int = Field(default=ALL_DAYS_MASK, ge=1, le=ALL_DAYS_MASK)
    # When set, recurrence is "daily at this minute-of-day" (in `timezone`)
    # and `interval_seconds` is ignored for cadence.
    run_at_minute: int | None = Field(default=None, ge=0, le=MAX_MINUTE_OF_DAY)
    timezone: str = Field(default="UTC", max_length=64)
    clear_context: bool = False
    enabled: bool = True


class TopicScheduleCreate(BaseModel):
    """Attach a recurrence to an existing topic (no title — the topic owns it)."""

    prompt: str = Field(min_length=1)
    interval_seconds: int = Field(ge=MIN_INTERVAL_SECONDS)
    days_of_week: int = Field(default=ALL_DAYS_MASK, ge=1, le=ALL_DAYS_MASK)
    run_at_minute: int | None = Field(default=None, ge=0, le=MAX_MINUTE_OF_DAY)
    timezone: str = Field(default="UTC", max_length=64)
    clear_context: bool = False
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    # Title edits the underlying topic; the rest edit the schedule row.
    title: str | None = Field(default=None, min_length=1, max_length=255)
    prompt: str | None = Field(default=None, min_length=1)
    interval_seconds: int | None = Field(default=None, ge=MIN_INTERVAL_SECONDS)
    days_of_week: int | None = Field(default=None, ge=1, le=ALL_DAYS_MASK)
    # Pass an int to switch to / update daily-at-time mode; pass null to clear
    # it and return to interval mode. Use `model_fields_set` to distinguish
    # "omitted" from "explicit null" in the router.
    run_at_minute: int | None = Field(default=None, ge=0, le=MAX_MINUTE_OF_DAY)
    timezone: str | None = Field(default=None, max_length=64)
    clear_context: bool | None = None
    enabled: bool | None = None
