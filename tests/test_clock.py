from __future__ import annotations

from datetime import UTC, datetime, timedelta

from precursor.backend.clock import utcnow
from precursor.backend.models.base import TimestampMixin


def test_utcnow_is_aware_utc() -> None:
    before = datetime.now(UTC)
    now = utcnow()
    assert now.tzinfo is UTC
    assert before <= now <= before + timedelta(seconds=5)


def test_model_timestamps_use_the_shared_clock() -> None:
    created = TimestampMixin.__dict__["created_at"].column
    updated = TimestampMixin.__dict__["updated_at"].column
    # SQLAlchemy wraps a callable default; the wrapper keeps the original.
    for default in (created.default, updated.default, updated.onupdate):
        assert default.arg.__wrapped__ is utcnow
