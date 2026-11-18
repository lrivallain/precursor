"""The backend's clock: one place that says what "now" is.

Every timestamp Precursor stores or compares is timezone-aware UTC. Going
through ``utcnow()`` keeps that true everywhere, with one definition instead
of a helper per module. ``windows_updater.py`` keeps its own call, because it must
not import ``precursor`` while the package is being replaced.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """The current time as a timezone-aware UTC ``datetime``."""
    return datetime.now(UTC)
