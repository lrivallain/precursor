"""Recurrence timing helpers for scheduled topics.

Kept in its own module so both the schedules router and the background
scheduler can compute the next run time without importing each other.

Two recurrence modes are supported:

* **Interval** (``run_at_minute is None``): run every ``interval_seconds``,
  skipping disallowed weekdays.
* **Daily-at-time** (``run_at_minute`` set): run once per allowed day at a
  fixed wall-clock time (e.g. 07:00) in the schedule's timezone. DST is
  handled by ``zoneinfo`` so "07:00 local" stays 07:00 across the change.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

ALL_DAYS_MASK = 127  # bits 0..6 set => every day of the week
MINUTES_PER_DAY = 24 * 60


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
