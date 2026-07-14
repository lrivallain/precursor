"""Shared scaffolding for the app's single-task background pollers.

Several services run the same shape: a lightweight task that wakes every
``poll`` seconds, does one unit of work, and sleeps again — gated by the
``scheduler_enabled`` flag and kept as a lazily-constructed singleton. This base
captures the start/stop/loop plumbing so each poller only supplies its poll
cadence and a ``run_once`` work function.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod

from precursor.backend.config import Settings, get_settings

logger = logging.getLogger(__name__)


class BackgroundPoll(ABC):
    """Base for a gated, single-task background poller.

    Subclasses set ``task_name`` (asyncio task name) and ``label`` (human label
    used in log lines), optionally override ``poll_floor``, and implement
    ``poll_seconds`` and ``run_once``.
    """

    #: asyncio task name (e.g. ``"backup-ticker"``).
    task_name: str = "background-poll"
    #: Human-readable label used in log messages (e.g. ``"Backup ticker"``).
    label: str = "Background poller"
    #: Lower bound applied to ``poll_seconds`` so a tiny setting can't busy-spin.
    poll_floor: int = 60

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    @abstractmethod
    def poll_seconds(self) -> int:
        """Configured poll interval (before ``poll_floor`` is applied)."""

    @abstractmethod
    async def run_once(self) -> None:
        """Perform one poll's worth of work."""

    def _on_start(self) -> None:
        """Log a start line. Override to include extra context."""
        logger.info("%s started (poll=%ss).", self.label, self.poll_seconds)

    async def start(self) -> None:
        if self._running or not self._settings.scheduler_enabled:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name=self.task_name)
        self._on_start()

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        poll = max(self.poll_floor, self.poll_seconds)
        while self._running:
            try:
                await self.run_once()
            except Exception:
                logger.exception("%s iteration failed", self.label)
            try:
                await asyncio.sleep(poll)
            except asyncio.CancelledError:
                break

    async def nudge(self) -> None:
        """Run one unit of work immediately (e.g. just after a config change)."""
        if not self._running:
            return
        try:
            await self.run_once()
        except Exception:
            logger.exception("%s nudge failed", self.label)
