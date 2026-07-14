"""Background ticker that fires due one-shot reminders.

A single lightweight task polls the DB every ``reminder_poll_seconds`` for
reminders whose time has come and fires them (see ``services/reminders``).
Unlike the scheduler, firing is pure DB writes + event publishes (no LLM work),
so there's no worker pool — the ticker fires them inline and returns quickly.

A ``nudge`` lets a just-created near-term reminder fire without waiting for the
next poll tick.
"""

from __future__ import annotations

from precursor.backend.db import SessionLocal
from precursor.backend.services.background_poll import BackgroundPoll
from precursor.backend.services.reminders import fire_due


class ReminderTicker(BackgroundPoll):
    task_name = "reminder-ticker"
    label = "Reminder ticker"
    poll_floor = 5

    @property
    def poll_seconds(self) -> int:
        return self._settings.reminder_poll_seconds

    async def run_once(self) -> None:
        async with SessionLocal() as session:
            await fire_due(session)


_ticker: ReminderTicker | None = None


def get_reminder_ticker() -> ReminderTicker:
    global _ticker
    if _ticker is None:
        _ticker = ReminderTicker()
    return _ticker
