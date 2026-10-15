"""Background ticker that settles approvals an unattended run was left waiting on.

Each pass denies the parked calls past their deadline (see
``services/unattended_approvals``) and resumes the affected turns headlessly.
Resumes run as their own tasks so a long model turn never delays the next pass.
"""

from __future__ import annotations

import asyncio
import logging
import time

from precursor.backend.services.background_poll import BackgroundPoll
from precursor.backend.services.unattended_approvals import expire_overdue, resume_headless

logger = logging.getLogger(__name__)

_STARTUP_DELAY_SECONDS = 3.0
_STOP_GRACE_SECONDS = 5.0


class ApprovalExpiryTicker(BackgroundPoll):
    task_name = "approval-expiry-ticker"
    label = "Approval expiry ticker"
    poll_floor = 5

    def __init__(self) -> None:
        super().__init__()
        self._started_at = 0.0
        self._busy = False
        self._resumes: set[asyncio.Task[None]] = set()

    @property
    def poll_seconds(self) -> int:
        return 30

    async def start(self) -> None:
        self._started_at = time.monotonic()
        await super().start()

    async def stop(self) -> None:
        # A cancelled SQLite transaction can leave its pooled connection holding
        # the write lock, so let an in-flight pass commit before cancelling.
        self._running = False
        deadline = time.monotonic() + _STOP_GRACE_SECONDS
        while self._busy and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        for task in list(self._resumes):
            task.cancel()
        await super().stop()

    async def run_once(self) -> None:
        if time.monotonic() - self._started_at < _STARTUP_DELAY_SECONDS:
            return
        self._busy = True
        try:
            topic_ids = await expire_overdue()
        finally:
            self._busy = False
        for topic_id in topic_ids:
            task = asyncio.create_task(
                self._resume(topic_id), name=f"approval-expiry-resume-{topic_id}"
            )
            self._resumes.add(task)
            task.add_done_callback(self._resumes.discard)

    @staticmethod
    async def _resume(topic_id: int) -> None:
        try:
            await resume_headless(topic_id)
        except Exception:
            logger.exception("Headless resume of topic %s failed", topic_id)


_ticker: ApprovalExpiryTicker | None = None


def get_approval_expiry_ticker() -> ApprovalExpiryTicker:
    global _ticker
    if _ticker is None:
        _ticker = ApprovalExpiryTicker()
    return _ticker
