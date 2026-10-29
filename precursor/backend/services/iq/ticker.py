"""Background ticker that keeps the Precursor IQ index current.

Each pass drains the change queue, vectorises new chunks when embeddings are
on, and — on a slower cadence — runs the reconcile sweep that catches writes
the flush hook can't see. Gated by ``scheduler_enabled`` like the other tickers;
retrieval still drains a bounded batch on its own when the ticker is off.
"""

from __future__ import annotations

import asyncio
import time

from precursor.backend.services.background_poll import BackgroundPoll
from precursor.backend.services.iq import indexer

# How long one pass may keep draining before yielding to the next tick.
_DRAIN_BUDGET_SECONDS = 10.0
_STARTUP_DELAY_SECONDS = 3.0
_STOP_GRACE_SECONDS = 5.0


class IQTicker(BackgroundPoll):
    task_name = "iq-ticker"
    label = "Precursor IQ indexer"
    poll_floor = 5

    def __init__(self) -> None:
        super().__init__()
        self._last_reconcile = 0.0
        self._started_at = 0.0
        self._busy = False

    @property
    def poll_seconds(self) -> int:
        return self._settings.iq_index_poll_seconds

    async def start(self) -> None:
        if not self._settings.iq_enabled:
            return
        now = time.monotonic()
        # The first sweep runs a full reconcile interval after startup.
        self._last_reconcile = now
        self._started_at = now
        await super().start()

    async def stop(self) -> None:
        # Let an in-flight batch commit rather than cancelling it mid-write: a
        # cancelled SQLite transaction can leave its pooled connection holding
        # the write lock, which the next writer then times out on.
        self._running = False
        deadline = time.monotonic() + _STOP_GRACE_SECONDS
        while self._busy and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await super().stop()

    async def run_once(self) -> None:
        # Stay out of the way while the app is still starting up; queries
        # index what they need on their own in the meantime.
        if time.monotonic() - self._started_at < _STARTUP_DELAY_SECONDS:
            return
        self._busy = True
        try:
            await indexer.drain(
                max_sources=5_000,
                budget_seconds=_DRAIN_BUDGET_SECONDS,
                keep_going=lambda: self._running,
            )
            if not self._running:
                return
            await indexer.embed_pending()
            now = time.monotonic()
            if self._running and (
                now - self._last_reconcile >= self._settings.iq_reconcile_poll_seconds
            ):
                self._last_reconcile = now
                await indexer.reconcile()
        finally:
            self._busy = False


_ticker: IQTicker | None = None


def get_iq_ticker() -> IQTicker:
    global _ticker
    if _ticker is None:
        _ticker = IQTicker()
    return _ticker
