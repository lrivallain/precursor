"""Background ticker that fires due one-shot reminders.

A single lightweight task polls the DB every ``reminder_poll_seconds`` for
reminders whose time has come and fires them (see ``services/reminders``).
Unlike the scheduler, firing is pure DB writes + event publishes (no LLM work),
so there's no worker pool — the ticker fires them inline and returns quickly.

A ``nudge`` lets a just-created near-term reminder fire without waiting for the
next poll tick.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from precursor.backend.config import Settings, get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.services.reminders import fire_due

logger = logging.getLogger(__name__)


class ReminderTicker:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        if self._running or not self._settings.scheduler_enabled:
            return
        self._running = True
        self._task = asyncio.create_task(self._ticker(), name="reminder-ticker")
        logger.info(
            "Reminder ticker started (poll=%ss).",
            self._settings.reminder_poll_seconds,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _ticker(self) -> None:
        poll = max(5, self._settings.reminder_poll_seconds)
        while self._running:
            try:
                await self._fire_once()
            except Exception:
                logger.exception("Reminder ticker iteration failed")
            try:
                await asyncio.sleep(poll)
            except asyncio.CancelledError:
                break

    async def _fire_once(self) -> None:
        async with SessionLocal() as session:
            await fire_due(session)

    async def nudge(self) -> None:
        """Fire any now-due reminders immediately (e.g. after creating one)."""
        if not self._running:
            return
        try:
            await self._fire_once()
        except Exception:
            logger.exception("Reminder ticker nudge failed")


_ticker: ReminderTicker | None = None


def get_reminder_ticker() -> ReminderTicker:
    global _ticker
    if _ticker is None:
        _ticker = ReminderTicker()
    return _ticker
