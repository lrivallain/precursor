"""RecurrenceMixin — the "when does this run" half of a schedulable row.

Topic schedules, agent schedules and workflows all answer the same question
("when does this fire next?") with the same four columns, so the storage and
the accessors live here once.

A schedule may hold **several** recurrence rules at once — "every day at 07:00"
*plus* "every weekday at 12:00". The first rule stays in the owner's own
columns (so every existing reader, migration and API payload keeps working),
and any additional rules are JSON-encoded into ``extra_rules``. The scheduler
never reads the rules to *find* due rows — it only ever queries the materialised
``next_run_at`` — so a JSON column costs nothing at poll time.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.services.schedule_timing import (
    ALL_DAYS_MASK,
    DEFAULT_INTERVAL_SECONDS,
    RecurrenceRule,
    compute_next_run_multi,
    normalize_rules,
    rules_from_json,
    rules_to_json,
)


class RecurrenceMixin:
    """Multi-rule recurrence accessors over the primary columns + ``extra_rules``.

    Implementors must provide ``interval_seconds``, ``days_of_week``,
    ``run_at_minute`` and ``timezone`` mapped columns.
    """

    # JSON-encoded list of the *additional* rules beyond the primary one held in
    # the columns above. Null/empty => a single-rule schedule.
    extra_rules: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Declared for typing only; the concrete models map these.
    interval_seconds: Mapped[int] | Mapped[int | None]
    days_of_week: Mapped[int]
    run_at_minute: Mapped[int | None]
    timezone: Mapped[str]

    @property
    def recurrence_rules(self) -> list[RecurrenceRule]:
        """Every rule this schedule fires on, primary first."""
        primary = RecurrenceRule(
            interval_seconds=int(self.interval_seconds or DEFAULT_INTERVAL_SECONDS),
            days_of_week=int(self.days_of_week or ALL_DAYS_MASK),
            run_at_minute=self.run_at_minute,
            timezone=self.timezone or "UTC",
        )
        return normalize_rules([primary, *rules_from_json(self.extra_rules)])

    def set_recurrence_rules(self, rules: Sequence[RecurrenceRule]) -> None:
        """Replace the rule set. An empty list leaves the schedule untouched."""
        clean = normalize_rules(rules)
        if not clean:
            return
        primary, *extras = clean
        self.interval_seconds = primary.interval_seconds
        self.days_of_week = primary.days_of_week
        self.run_at_minute = primary.run_at_minute
        self.timezone = primary.timezone
        self.extra_rules = rules_to_json(extras)

    def next_run_after(self, from_time: datetime) -> datetime | None:
        """Earliest next fire time across every rule (UTC)."""
        return compute_next_run_multi(from_time, self.recurrence_rules)
