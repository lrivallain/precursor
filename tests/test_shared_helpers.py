"""Tests for small shared helpers: ``get_or_404`` and ``BackgroundPoll.instance``.

Many routers and services rely on these instead of repeating the code, so their
contract (404 message, pass-through options, one instance per poller class) is
pinned here.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from precursor.backend.db import SessionLocal
from precursor.backend.lookups import get_or_404
from precursor.backend.models import Topic
from precursor.backend.services.background_poll import BackgroundPoll
from precursor.backend.services.backup_ticker import BackupTicker, get_backup_ticker
from precursor.backend.services.reminder_ticker import get_reminder_ticker
from tests.support import init_db


def test_get_or_404_returns_the_row() -> None:
    init_db()

    async def run() -> str:
        async with SessionLocal() as session:
            topic = Topic(title="Lookup target", slug="lookup-target")
            session.add(topic)
            await session.commit()
            found = await get_or_404(session, Topic, topic.id)
            return found.title

    assert asyncio.run(run()) == "Lookup target"


@pytest.mark.parametrize(
    ("detail", "expected"),
    [(None, "Topic not found"), ("No such topic here", "No such topic here")],
)
def test_get_or_404_raises_with_detail(detail: str | None, expected: str) -> None:
    init_db()

    async def run() -> None:
        async with SessionLocal() as session:
            await get_or_404(session, Topic, 987_654_321, detail)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(run())
    assert caught.value.status_code == 404
    assert caught.value.detail == expected


def test_background_poll_instance_is_one_per_class() -> None:
    assert get_backup_ticker() is get_backup_ticker()
    assert get_backup_ticker() is BackupTicker.instance()
    assert isinstance(get_backup_ticker(), BackupTicker)
    assert get_reminder_ticker() is not get_backup_ticker()
    assert BackgroundPoll._instances[BackupTicker] is get_backup_ticker()
