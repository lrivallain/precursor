"""Schedule schemas — recurrence config for scheduled topics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated, ClassVar, Self

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)

from precursor.backend.services.schedule_timing import (
    DEFAULT_INTERVAL_SECONDS,
    MAX_RULES,
)
from precursor.backend.services.schedule_timing import (
    RecurrenceRule as TimingRule,
)


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


class RecurrenceRule(BaseModel):
    """One "when to run" clause of a schedule.

    A schedule fires at the *earliest* next occurrence across all of its rules,
    so combining "every day at 07:00" with "every weekday at 12:00" gives both.
    """

    model_config = ConfigDict(from_attributes=True)

    interval_seconds: int = Field(default=DEFAULT_INTERVAL_SECONDS, ge=MIN_INTERVAL_SECONDS)
    days_of_week: int = Field(default=ALL_DAYS_MASK, ge=1, le=ALL_DAYS_MASK)
    # When set, this rule is "daily at this minute-of-day" (in `timezone`) and
    # `interval_seconds` is ignored for its cadence.
    run_at_minute: int | None = Field(default=None, ge=0, le=MAX_MINUTE_OF_DAY)
    timezone: str = Field(default="UTC", max_length=64)

    def to_timing(self) -> TimingRule:
        return TimingRule(
            interval_seconds=self.interval_seconds,
            days_of_week=self.days_of_week,
            run_at_minute=self.run_at_minute,
            timezone=self.timezone,
        )


# Read models source this from the ORM's ``recurrence_rules`` property but
# expose it as ``rules``.
RuleListRead = Annotated[
    list[RecurrenceRule],
    Field(
        default_factory=list,
        validation_alias=AliasChoices("rules", "recurrence_rules"),
    ),
]

# Write models accept an optional list; ``None`` means "leave the rules alone".
RuleListWrite = Annotated[list[RecurrenceRule] | None, Field(default=None, max_length=MAX_RULES)]


class RulesPayload(BaseModel):
    """Shared normalisation for payloads that can carry a rule list.

    Clients may send either the legacy flat recurrence fields or a ``rules``
    list. When both are present the list wins, so a client that upgrades to
    multi-rule doesn't have to keep the flat fields in sync.
    """

    # Set on create payloads, where omitting the cadence entirely is an error.
    _cadence_required: ClassVar[bool] = False

    # Declared here so the shared validator and `resolved_rules` can rely on it;
    # every concrete payload re-declares it to attach its own docs/limits.
    rules: RuleListWrite

    def resolved_rules(self) -> list[TimingRule] | None:
        """The rule set this payload asks for, or None to leave it unchanged."""
        if self.rules:
            return [rule.to_timing() for rule in self.rules]
        if not self._touched_flat_fields():
            return None
        return [
            TimingRule(
                interval_seconds=self._flat_interval() or DEFAULT_INTERVAL_SECONDS,
                days_of_week=self._flat_days() or ALL_DAYS_MASK,
                run_at_minute=self._flat_minute(),
                timezone=self._flat_timezone() or "UTC",
            )
        ]

    def merged_rules(self, current: Sequence[TimingRule]) -> list[TimingRule] | None:
        """``current`` after applying this payload, or None if it changes nothing.

        A ``rules`` list replaces the whole set. The legacy flat fields instead
        *patch the primary rule* and leave any extras alone, so an old client
        that PATCHes only ``interval_seconds`` doesn't silently drop the extra
        rules someone added in the UI.
        """
        if self.rules:
            return [rule.to_timing() for rule in self.rules]

        touched = self._touched_flat_fields()
        if not touched:
            return None
        primary = current[0] if current else TimingRule()
        patched = replace(
            primary,
            interval_seconds=self._flat_interval() or primary.interval_seconds,
            days_of_week=self._flat_days() or primary.days_of_week,
            timezone=self._flat_timezone() or primary.timezone,
            # `run_at_minute` is tri-state: an explicit null means "back to
            # interval mode", so it is applied even when None.
            run_at_minute=(
                self._flat_minute() if "run_at_minute" in touched else primary.run_at_minute
            ),
        )
        return [patched, *current[1:]]

    def _touched_flat_fields(self) -> set[str]:
        """Which legacy recurrence fields this payload explicitly carries."""
        return self.model_fields_set & {
            "interval_seconds",
            "days_of_week",
            "run_at_minute",
            "timezone",
        }

    # Concrete payloads declare these with differing optionality, so they are
    # read defensively and re-typed here for the shared logic above.
    def _flat_interval(self) -> int | None:
        value: int | None = getattr(self, "interval_seconds", None)
        return value

    def _flat_days(self) -> int | None:
        value: int | None = getattr(self, "days_of_week", None)
        return value

    def _flat_minute(self) -> int | None:
        value: int | None = getattr(self, "run_at_minute", None)
        return value

    def _flat_timezone(self) -> str | None:
        value: str | None = getattr(self, "timezone", None)
        return value

    @model_validator(mode="after")
    def _require_a_cadence(self) -> Self:
        """A create payload must describe a cadence one way or the other."""
        if "rules" in self.model_fields_set and self.rules is not None and not self.rules:
            raise ValueError("rules must not be empty")
        if not self._cadence_required:
            return self
        if self.rules or getattr(self, "interval_seconds", None) is not None:
            return self
        raise ValueError("Provide interval_seconds or a non-empty rules list")


class ScheduleSummary(BaseModel):
    """Compact schedule view embedded in the sidebar TopicNode."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    enabled: bool
    # The primary rule, kept flat for backwards compatibility. `rules` is the
    # complete set (primary first) and is what multi-rule clients should read.
    interval_seconds: int
    days_of_week: int = ALL_DAYS_MASK
    run_at_minute: int | None = None
    timezone: str = "UTC"
    rules: RuleListRead
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


class ScheduleCreate(RulesPayload):
    """Create a scheduled topic + its recurrence in one call."""

    _cadence_required = True

    title: str = Field(min_length=1, max_length=255)
    prompt: str = Field(min_length=1)
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
    clear_context: bool = False
    enabled: bool = True


class TopicScheduleCreate(RulesPayload):
    """Attach a recurrence to an existing topic (no title — the topic owns it)."""

    _cadence_required = True

    prompt: str = Field(min_length=1)
    interval_seconds: int | None = Field(default=None, ge=MIN_INTERVAL_SECONDS)
    days_of_week: int = Field(default=ALL_DAYS_MASK, ge=1, le=ALL_DAYS_MASK)
    run_at_minute: int | None = Field(default=None, ge=0, le=MAX_MINUTE_OF_DAY)
    timezone: str = Field(default="UTC", max_length=64)
    rules: RuleListWrite
    clear_context: bool = False
    enabled: bool = True


class ScheduleUpdate(RulesPayload):
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
    # Replaces the whole rule set when supplied (an empty list is rejected —
    # delete the schedule instead of leaving it with nothing to fire on).
    rules: RuleListWrite
    clear_context: bool | None = None
    enabled: bool | None = None
