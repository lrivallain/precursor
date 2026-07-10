"""Background ticker that prunes expired TOOL-result content.

A single lightweight task periodically replaces the ``content`` of aged TOOL
rows with a short placeholder (see ``services/tool_result_retention``), bounding
long-term DB growth. Gated by the same ``scheduler_enabled`` flag as the other
tickers; the poll interval defaults to daily. When retention is disabled the
sweep is a cheap no-op, so the ticker can keep running regardless.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from precursor.backend.config import Settings, get_settings
from precursor.backend.services.tool_result_retention import prune_expired_tool_results

logger = logging.getLogger(__name__)


class ToolResultTicker:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        if self._running or not self._settings.scheduler_enabled:
            return
        self._running = True
        self._task = asyncio.create_task(self._ticker(), name="tool-result-ticker")
        logger.info(
            "Tool-result retention ticker started (poll=%ss).",
            self._settings.tool_result_retention_poll_seconds,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _ticker(self) -> None:
        poll = max(60, self._settings.tool_result_retention_poll_seconds)
        while self._running:
            try:
                await prune_expired_tool_results()
            except Exception:
                logger.exception("Tool-result retention ticker iteration failed")
            try:
                await asyncio.sleep(poll)
            except asyncio.CancelledError:
                break


_ticker: ToolResultTicker | None = None


def get_tool_result_ticker() -> ToolResultTicker:
    global _ticker
    if _ticker is None:
        _ticker = ToolResultTicker()
    return _ticker
