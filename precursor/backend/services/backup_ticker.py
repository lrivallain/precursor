"""Background ticker that runs the folder backup on a daily cadence.

A single lightweight task polls every ``backup_poll_seconds`` and, when the
user has enabled backups (Settings → Backup) and ``backup_interval_seconds``
has elapsed since the last successful run, performs one backup (see
``services/backup``). Gated by the same ``scheduler_enabled`` flag as the other
tickers; when backups are disabled every poll is a cheap no-op, so the ticker
can keep running regardless.

A ``nudge`` lets a just-enabled backup run without waiting for the next poll.
"""

from __future__ import annotations

import logging

from precursor.backend.db import SessionLocal
from precursor.backend.services.background_poll import BackgroundPoll
from precursor.backend.services.backup import backup_due, run_backup

logger = logging.getLogger(__name__)


class BackupTicker(BackgroundPoll):
    task_name = "backup-ticker"
    label = "Backup ticker"
    poll_floor = 60

    @property
    def poll_seconds(self) -> int:
        return self._settings.backup_poll_seconds

    def _on_start(self) -> None:
        logger.info(
            "Backup ticker started (poll=%ss, interval=%ss).",
            self._settings.backup_poll_seconds,
            self._settings.backup_interval_seconds,
        )

    async def run_once(self) -> None:
        async with SessionLocal() as session:
            due = await backup_due(session, self._settings.backup_interval_seconds)
        if due:
            await run_backup()


_ticker: BackupTicker | None = None


def get_backup_ticker() -> BackupTicker:
    global _ticker
    if _ticker is None:
        _ticker = BackupTicker()
    return _ticker
