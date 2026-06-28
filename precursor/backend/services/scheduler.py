"""Background scheduler for recurring "scheduled" topics.

Design (see also the per-topic isolation notes in the PR):

* A single **ticker** task polls the DB every ``poll_seconds`` for schedules
  that are due (``enabled`` and ``next_run_at <= now``). It does no LLM work
  itself — it only *claims* due rows and hands them to a queue, so it always
  returns quickly and never blocks the event loop.
* A small, fixed pool of **worker** tasks drains the queue. The pool size caps
  how many scheduled runs execute concurrently, so a burst of due schedules can
  never starve manual chats (which never touch this queue).
* Claiming is done at the data layer with a ``status='running'`` + ``lease_until``
  lease. A conditional UPDATE guarded by ``rowcount`` guarantees only one worker
  wins a row, which is also correct across multiple processes. Rows stuck in
  ``running`` past their lease are reclaimed (process-crash recovery).
* Each run is wrapped in a timeout + try/except so one hung or failing run can
  never take down the ticker or sibling runs; it records ``status='error'`` and
  still advances ``next_run_at`` so it self-heals next cycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult

from precursor.backend.config import Settings, get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import TopicSchedule
from precursor.backend.services.app_settings import (
    resolve_scheduled_run_timeout_seconds,
)
from precursor.backend.services.events import publish_topic_changed
from precursor.backend.services.schedule_timing import compute_next_run
from precursor.backend.services.scheduled_commands import run_scheduled_prompt_with_timeout

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


class Scheduler:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        # The queue is created in start() so it binds to the running event loop
        # (a singleton constructed on one loop must not be reused on another).
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._tasks: list[asyncio.Task[None]] = []
        # Topic ids currently claimed/in-flight, so the ticker doesn't re-enqueue
        # a row a worker is still finishing within the same process.
        self._inflight: set[int] = set()
        # Topic ids whose next run was explicitly forced via "Run now". A forced
        # run bypasses the prompt's /guard emptiness gate (the user asked for it
        # now), while still honouring the auth gate. Consumed once per run.
        self._forced: set[int] = set()
        self._running = False

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if self._running or not self._settings.scheduler_enabled:
            if not self._settings.scheduler_enabled:
                logger.info("Scheduler disabled via settings.")
            return
        self._running = True
        self._queue = asyncio.Queue()
        self._inflight.clear()
        self._forced.clear()
        # Reclaim any rows orphaned by a previous crash before we begin.
        await self._reclaim_orphans()
        self._tasks.append(asyncio.create_task(self._ticker(), name="scheduler-ticker"))
        for i in range(max(1, self._settings.scheduler_concurrency)):
            self._tasks.append(asyncio.create_task(self._worker(), name=f"scheduler-worker-{i}"))
        logger.info(
            "Scheduler started (poll=%ss, workers=%s).",
            self._settings.scheduler_poll_seconds,
            self._settings.scheduler_concurrency,
        )

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()

    # -- ticker ------------------------------------------------------------
    async def _ticker(self) -> None:
        poll = max(5, self._settings.scheduler_poll_seconds)
        while self._running:
            try:
                await self._enqueue_due()
            except Exception:
                logger.exception("Scheduler ticker iteration failed")
            try:
                await asyncio.sleep(poll)
            except asyncio.CancelledError:
                break

    async def _enqueue_due(self) -> None:
        now = _now()
        async with SessionLocal() as session:
            result = await session.execute(
                select(TopicSchedule.id, TopicSchedule.topic_id).where(
                    TopicSchedule.enabled.is_(True),
                    TopicSchedule.next_run_at.is_not(None),
                    TopicSchedule.next_run_at <= now,
                    TopicSchedule.status != "running",
                )
            )
            due = [(row[0], row[1]) for row in result.all()]

        for schedule_id, topic_id in due:
            if topic_id in self._inflight:
                continue
            if await self._claim(schedule_id):
                self._inflight.add(topic_id)
                await self._queue.put(topic_id)

    async def nudge(self) -> None:
        """Run one enqueue pass immediately (e.g. after a 'Run now').

        Lets a just-due schedule fire without waiting for the next poll tick.
        No-op when the scheduler isn't running.
        """
        if not self._running:
            return
        try:
            await self._enqueue_due()
        except Exception:
            logger.exception("Scheduler nudge failed")

    def mark_forced(self, topic_id: int) -> None:
        """Flag a topic's next run as an explicit 'Run now'.

        A forced run bypasses the prompt's ``/guard`` emptiness gate so an
        intentional manual trigger always starts (the auth gate still applies).
        The flag is consumed by the next :meth:`_run_one` for this topic.
        """
        self._forced.add(topic_id)

    async def _claim(self, schedule_id: int) -> bool:
        """Atomically mark a schedule running. Returns True if we won the claim."""
        now = _now()
        async with SessionLocal() as session:
            timeout = await resolve_scheduled_run_timeout_seconds(session)
            lease_until = now + timedelta(seconds=timeout + 30)
            result = await session.execute(
                update(TopicSchedule)
                .where(
                    TopicSchedule.id == schedule_id,
                    TopicSchedule.enabled.is_(True),
                    TopicSchedule.status != "running",
                )
                .values(status="running", lease_until=lease_until)
            )
            await session.commit()
            # A bulk UPDATE returns a CursorResult; rowcount tells us whether we
            # won the row. mypy types execute() loosely, hence the cast.
            return bool(cast("CursorResult[Any]", result).rowcount)

    async def _reclaim_orphans(self) -> None:
        """Reset rows stuck in 'running' with an expired lease (crash recovery)."""
        now = _now()
        async with SessionLocal() as session:
            await session.execute(
                update(TopicSchedule)
                .where(
                    TopicSchedule.status == "running",
                    TopicSchedule.lease_until.is_not(None),
                    TopicSchedule.lease_until < now,
                )
                .values(status="error", lease_until=None)
            )
            await session.commit()

    # -- workers -----------------------------------------------------------
    async def _worker(self) -> None:
        while self._running:
            try:
                topic_id = await self._queue.get()
            except asyncio.CancelledError:
                break
            try:
                await self._run_one(topic_id)
            except Exception:
                logger.exception("Scheduled run for topic %s crashed", topic_id)
            finally:
                self._inflight.discard(topic_id)
                self._queue.task_done()

    async def _run_one(self, topic_id: int) -> None:
        # Reload the schedule so we use the freshest prompt/interval.
        async with SessionLocal() as session:
            result = await session.execute(
                select(TopicSchedule).where(TopicSchedule.topic_id == topic_id)
            )
            schedule = result.scalar_one_or_none()
            if schedule is None or not schedule.enabled:
                return
            prompt = schedule.prompt
            interval = schedule.interval_seconds
            days_mask = schedule.days_of_week
            run_at_minute = schedule.run_at_minute
            tz_name = schedule.timezone
            clear_context = schedule.clear_context
            run_timeout = await resolve_scheduled_run_timeout_seconds(session)

        status = "ok"
        error: str | None = None
        # Consume any "Run now" flag for this topic so a forced run bypasses the
        # guard's emptiness gate exactly once; later automatic ticks gate normally.
        forced = topic_id in self._forced
        self._forced.discard(topic_id)
        try:
            await run_scheduled_prompt_with_timeout(
                topic_id,
                prompt,
                timeout=float(run_timeout),
                clear_context=clear_context,
                force=forced,
            )
        except TimeoutError:
            status, error = "error", "Run timed out."
            logger.warning("Scheduled run for topic %s timed out", topic_id)
        except Exception as exc:
            status, error = "error", str(exc)
            logger.exception("Scheduled run for topic %s failed", topic_id)

        now = _now()
        async with SessionLocal() as session:
            await session.execute(
                update(TopicSchedule)
                .where(TopicSchedule.topic_id == topic_id)
                .values(
                    status=status,
                    last_error=error,
                    last_run_at=now,
                    next_run_at=compute_next_run(now, interval, days_mask, run_at_minute, tz_name),
                    lease_until=None,
                )
            )
            await session.commit()
        await publish_topic_changed(topic_id)


_scheduler: Scheduler | None = None


def get_scheduler() -> Scheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler
