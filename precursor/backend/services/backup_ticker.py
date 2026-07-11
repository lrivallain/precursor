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

import asyncio
import contextlib
import logging

from precursor.backend.config import Settings, get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.services.backup import backup_due, run_backup

logger = logging.getLogger(__name__)


class BackupTicker:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        if self._running or not self._settings.scheduler_enabled:
            return
        self._running = True
        self._task = asyncio.create_task(self._ticker(), name="backup-ticker")
        logger.info(
            "Backup ticker started (poll=%ss, interval=%ss).",
            self._settings.backup_poll_seconds,
            self._settings.backup_interval_seconds,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _ticker(self) -> None:
        poll = max(60, self._settings.backup_poll_seconds)
        while self._running:
            try:
                await self._run_if_due()
            except Exception:
                logger.exception("Backup ticker iteration failed")
            try:
                await asyncio.sleep(poll)
            except asyncio.CancelledError:
                break

    async def _run_if_due(self) -> None:
        async with SessionLocal() as session:
            due = await backup_due(session, self._settings.backup_interval_seconds)
        if due:
            await run_backup()

    async def nudge(self) -> None:
        """Run a backup now if one is due (e.g. just after enabling it)."""
        if not self._running:
            return
        try:
            await self._run_if_due()
        except Exception:
            logger.exception("Backup ticker nudge failed")


_ticker: BackupTicker | None = None


def get_backup_ticker() -> BackupTicker:
    global _ticker
    if _ticker is None:
        _ticker = BackupTicker()
    return _ticker
