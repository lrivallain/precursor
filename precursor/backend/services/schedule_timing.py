"""Recurrence timing helpers for scheduled topics.

Kept in its own module so both the schedules router and the background
scheduler can compute the next run time without importing each other.

Two recurrence modes are supported:

* **Interval** (``run_at_minute is None``): run every ``interval_seconds``,
  skipping disallowed weekdays.
* **Daily-at-time** (``run_at_minute`` set): run once per allowed day at a
  fixed wall-clock time (e.g. 07:00) in the schedule's timezone. DST is
  handled by ``zoneinfo`` so "07:00 local" stays 07:00 across the change.

A schedule may carry **several** such rules at once — "every day at 07:00"
*plus* "every weekday at 12:00" — in which case the owner fires at the
earliest next occurrence across the whole set (see :func:`compute_next_run_multi`).
The first rule lives in the owner's own columns; any extras are JSON-encoded
into its ``extra_rules`` column.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

ALL_DAYS_MASK = 127  # bits 0..6 set => every day of the week
MINUTES_PER_DAY = 24 * 60
# Guard rail shared with the schemas: never run more often than once a minute.
MIN_INTERVAL_SECONDS = 60
# Cadence used when a rule carries no usable interval (workflows allow null).
DEFAULT_INTERVAL_SECONDS = 86400
# Upper bound on rules per schedule. Generous for real use, but bounded so a
# malformed payload can't make the ticker walk an unbounded list every poll.
MAX_RULES = 20


@dataclass(frozen=True, slots=True)
class RecurrenceRule:
    """One "when to run" clause of a schedule.

    Mirrors the four recurrence columns an owner (topic/agent schedule row, or
    a workflow) has always had, so a single-rule schedule is just a one-element
    list and nothing about the existing semantics changes.
    """

    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    days_of_week: int = ALL_DAYS_MASK
    run_at_minute: int | None = None
    timezone: str = "UTC"

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            "interval_seconds": self.interval_seconds,
            "days_of_week": self.days_of_week,
            "run_at_minute": self.run_at_minute,
            "timezone": self.timezone,
        }


def _zone(tz_name: str | None) -> ZoneInfo | timezone:
    """Resolve an IANA tz name, falling back to UTC on unknown/blank input."""
    if not tz_name:
        return UTC
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown schedule timezone %r; falling back to UTC", tz_name)
        return UTC


def _day_allowed(weekday: int, days_mask: int) -> bool:
    if days_mask <= 0 or days_mask >= ALL_DAYS_MASK:
        return True
    return bool((days_mask >> weekday) & 1)


def is_day_allowed(when: datetime, days_mask: int) -> bool:
    """Return True if ``when``'s weekday is permitted by ``days_mask``.

    ``days_mask`` is a 7-bit mask where bit 0 = Monday … bit 6 = Sunday,
    matching ``datetime.weekday()``. A mask of 0 (or the all-days mask) means
    no day restriction applies.
    """
    return _day_allowed(when.weekday(), days_mask)


def compute_next_run(
    from_time: datetime,
    interval_seconds: int,
    days_mask: int,
    run_at_minute: int | None = None,
    tz_name: str | None = None,
) -> datetime:
    """Compute the next run time after ``from_time`` (always returned in UTC).

    When ``run_at_minute`` is set, the schedule runs once per allowed day at
    that minute-of-day in ``tz_name``. Otherwise it runs every
    ``interval_seconds``, skipping disallowed weekdays.
    """
    if from_time.tzinfo is None:
        from_time = from_time.replace(tzinfo=UTC)

    if run_at_minute is not None:
        return _next_daily_run(from_time, run_at_minute, days_mask, tz_name)

    candidate = from_time + timedelta(seconds=interval_seconds)
    if days_mask <= 0 or days_mask >= ALL_DAYS_MASK:
        return candidate
    # At most 7 hops are ever needed to reach an allowed weekday.
    for _ in range(7):
        if _day_allowed(candidate.weekday(), days_mask):
            break
        candidate += timedelta(days=1)
    return candidate


def _next_daily_run(
    from_time: datetime, run_at_minute: int, days_mask: int, tz_name: str | None
) -> datetime:
    """Next occurrence of ``run_at_minute`` local time on an allowed weekday."""
    tz = _zone(tz_name)
    minute = max(0, min(run_at_minute, MINUTES_PER_DAY - 1))
    hour, minute_of_hour = divmod(minute, 60)

    local_now = from_time.astimezone(tz)
    candidate = local_now.replace(hour=hour, minute=minute_of_hour, second=0, microsecond=0)
    # If today's slot already passed (or is exactly now), start from tomorrow.
    if candidate <= local_now:
        candidate += timedelta(days=1)
    # Skip forward to the next allowed weekday (at most 7 hops).
    for _ in range(7):
        if _day_allowed(candidate.weekday(), days_mask):
            break
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


# ------------------------------------------------------------------ rule sets


def normalize_rule(rule: RecurrenceRule) -> RecurrenceRule:
    """Clamp a rule's fields into the ranges the timing maths assumes.

    Rules can arrive from the API, from a JSON column written by an older
    build, or from a plugin, so they are sanitised rather than trusted.
    """
    interval = int(rule.interval_seconds or DEFAULT_INTERVAL_SECONDS)
    interval = max(MIN_INTERVAL_SECONDS, interval)

    days = int(rule.days_of_week)
    if days <= 0 or days > ALL_DAYS_MASK:
        days = ALL_DAYS_MASK

    minute = rule.run_at_minute
    if minute is not None:
        minute = max(0, min(int(minute), MINUTES_PER_DAY - 1))

    return RecurrenceRule(
        interval_seconds=interval,
        days_of_week=days,
        run_at_minute=minute,
        timezone=rule.timezone or "UTC",
    )


def normalize_rules(rules: Iterable[RecurrenceRule]) -> list[RecurrenceRule]:
    """Sanitise, de-duplicate (order-preserving) and cap a rule list."""
    seen: set[tuple[int, int, int | None, str]] = set()
    out: list[RecurrenceRule] = []
    for rule in rules:
        clean = normalize_rule(rule)
        key = (clean.interval_seconds, clean.days_of_week, clean.run_at_minute, clean.timezone)
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
        if len(out) >= MAX_RULES:
            break
    return out


def rules_to_json(rules: Sequence[RecurrenceRule]) -> str | None:
    """Serialize *extra* rules for storage. Empty list => NULL column."""
    if not rules:
        return None
    return json.dumps([rule.as_dict() for rule in rules])


def rules_from_json(raw: str | None) -> list[RecurrenceRule]:
    """Parse an ``extra_rules`` column. Malformed content degrades to no extras."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring malformed extra_rules payload")
        return []
    if not isinstance(parsed, list):
        return []

    rules: list[RecurrenceRule] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            rules.append(
                RecurrenceRule(
                    interval_seconds=int(item.get("interval_seconds") or DEFAULT_INTERVAL_SECONDS),
                    days_of_week=int(item.get("days_of_week") or ALL_DAYS_MASK),
                    run_at_minute=(
                        None if item.get("run_at_minute") is None else int(item["run_at_minute"])
                    ),
                    timezone=str(item.get("timezone") or "UTC"),
                )
            )
        except (TypeError, ValueError):
            logger.warning("Ignoring malformed recurrence rule %r", item)
    return normalize_rules(rules)


def compute_next_run_multi(from_time: datetime, rules: Sequence[RecurrenceRule]) -> datetime | None:
    """Earliest next run across ``rules`` (UTC), or None when there are none.

    Each rule is evaluated independently from the *same* anchor, so combining
    "every day at 07:00" with "every weekday at 12:00" fires at whichever comes
    first — the schedule effectively unions its clauses.
    """
    candidates = [
        compute_next_run(
            from_time,
            rule.interval_seconds,
            rule.days_of_week,
            rule.run_at_minute,
            rule.timezone,
        )
        for rule in rules
    ]
    return min(candidates) if candidates else None
