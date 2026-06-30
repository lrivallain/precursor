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
from precursor.backend.models import AgentSchedule, TopicSchedule
from precursor.backend.services.app_settings import (
    resolve_agents_enabled,
    resolve_scheduled_run_timeout_seconds,
)
from precursor.backend.services.events import publish_agent_changed, publish_topic_changed
from precursor.backend.services.schedule_timing import compute_next_run
from precursor.backend.services.scheduled_commands import run_scheduled_prompt_with_timeout

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


# Agent statuses that mean a turn is in flight; a scheduled re-run is skipped
# (not errored) while one is active so it never stomps an unfinished run.
_AGENT_BUSY_STATUSES = {"pending", "running", "needs_approval"}


class _SkipRun(Exception):
    """Raised to skip a scheduled agent run without marking it failed."""


class Scheduler:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        # The queue is created in start() so it binds to the running event loop
        # (a singleton constructed on one loop must not be reused on another).
        # Items are ``(kind, target_id)`` where kind is "topic" (target=topic_id)
        # or "agent" (target=agent_session_id), so one ticker/worker pool drains
        # both scheduled topics and scheduled agents.
        self._queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        self._tasks: list[asyncio.Task[None]] = []
        # Items currently claimed/in-flight, so the ticker doesn't re-enqueue a
        # row a worker is still finishing within the same process.
        self._inflight: set[tuple[str, int]] = set()
        # Topic ids whose next run was explicitly forced via "Run now". The guard
        # still gates a forced run (an empty probe still skips), but the skip is
        # recorded visibly instead of silently so a manual trigger gives feedback.
        # Consumed once per run.
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
            topic_rows = (
                await session.execute(
                    select(TopicSchedule.id, TopicSchedule.topic_id).where(
                        TopicSchedule.enabled.is_(True),
                        TopicSchedule.next_run_at.is_not(None),
                        TopicSchedule.next_run_at <= now,
                        TopicSchedule.status != "running",
                    )
                )
            ).all()
            agent_rows = (
                await session.execute(
                    select(AgentSchedule.id, AgentSchedule.agent_session_id).where(
                        AgentSchedule.enabled.is_(True),
                        AgentSchedule.next_run_at.is_not(None),
                        AgentSchedule.next_run_at <= now,
                        AgentSchedule.status != "running",
                    )
                )
            ).all()

        due: list[tuple[str, int, int]] = [
            ("topic", schedule_id, target_id) for schedule_id, target_id in topic_rows
        ]
        due += [("agent", schedule_id, target_id) for schedule_id, target_id in agent_rows]

        for kind, schedule_id, target_id in due:
            item = (kind, target_id)
            if item in self._inflight:
                continue
            model = TopicSchedule if kind == "topic" else AgentSchedule
            if await self._claim(model, schedule_id):
                self._inflight.add(item)
                await self._queue.put(item)

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

        The guard still gates the run (an empty probe still skips), but a forced
        run records the skip visibly instead of silently, so the manual trigger
        gives feedback. The flag is consumed by the next :meth:`_run_one`.
        """
        self._forced.add(topic_id)

    async def _claim(self, model: type[TopicSchedule | AgentSchedule], schedule_id: int) -> bool:
        """Atomically mark a schedule running. Returns True if we won the claim."""
        now = _now()
        async with SessionLocal() as session:
            timeout = await resolve_scheduled_run_timeout_seconds(session)
            lease_until = now + timedelta(seconds=timeout + 30)
            result = await session.execute(
                update(model)
                .where(
                    model.id == schedule_id,
                    model.enabled.is_(True),
                    model.status != "running",
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
            for model in (TopicSchedule, AgentSchedule):
                await session.execute(
                    update(model)
                    .where(
                        model.status == "running",
                        model.lease_until.is_not(None),
                        model.lease_until < now,
                    )
                    .values(status="error", lease_until=None)
                )
            await session.commit()

    # -- workers -----------------------------------------------------------
    async def _worker(self) -> None:
        while self._running:
            try:
                item = await self._queue.get()
            except asyncio.CancelledError:
                break
            kind, target_id = item
            try:
                if kind == "agent":
                    await self._run_one_agent(target_id)
                else:
                    await self._run_one(target_id)
            except Exception:
                logger.exception("Scheduled %s run for %s crashed", kind, target_id)
            finally:
                self._inflight.discard(item)
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
        # Consume any "Run now" flag for this topic so a forced run records its
        # guard skip visibly (exactly once); later automatic ticks skip silently.
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

    async def _run_one_agent(self, agent_session_id: int) -> None:
        # Reload the schedule so we use the freshest cadence/options.
        async with SessionLocal() as session:
            result = await session.execute(
                select(AgentSchedule).where(AgentSchedule.agent_session_id == agent_session_id)
            )
            schedule = result.scalar_one_or_none()
            if schedule is None or not schedule.enabled:
                return
            interval = schedule.interval_seconds
            days_mask = schedule.days_of_week
            run_at_minute = schedule.run_at_minute
            tz_name = schedule.timezone
            clear_context = schedule.clear_context

        status = "ok"
        error: str | None = None
        try:
            await self._fire_agent_run(agent_session_id, clear_context=clear_context)
        except _SkipRun as exc:
            # Not a failure — a transient reason to skip this tick (e.g. the
            # previous run is still in flight). Surface it without an error state.
            error = str(exc)
            logger.info("Scheduled agent run for %s skipped: %s", agent_session_id, error)
        except Exception as exc:
            status, error = "error", str(exc)
            logger.exception("Scheduled agent run for %s failed", agent_session_id)

        now = _now()
        async with SessionLocal() as session:
            await session.execute(
                update(AgentSchedule)
                .where(AgentSchedule.agent_session_id == agent_session_id)
                .values(
                    status=status,
                    last_error=error,
                    last_run_at=now,
                    next_run_at=compute_next_run(now, interval, days_mask, run_at_minute, tz_name),
                    lease_until=None,
                )
            )
            await session.commit()
        await self._publish_agent(agent_session_id)

    async def _fire_agent_run(self, agent_session_id: int, *, clear_context: bool) -> None:
        """Trigger the agent's task once. Fire-and-forget: results post back via
        the event bus, so this returns as soon as the turn is submitted."""
        from precursor.backend.models import AgentSession
        from precursor.backend.services.agents import runtime
        from precursor.backend.services.agents.manager import get_agent_manager

        async with SessionLocal() as session:
            if not await resolve_agents_enabled(session):
                raise RuntimeError("Agents mode is disabled")
            agent = await session.get(AgentSession, agent_session_id)

        if agent is None:
            raise _SkipRun("Agent no longer exists")
        if agent.archived_at is not None:
            raise _SkipRun("Agent is archived")
        ok, detail = runtime.agents_available()
        if not ok:
            raise RuntimeError(f"Agents runtime unavailable: {detail}")
        if agent.status in _AGENT_BUSY_STATUSES:
            raise _SkipRun("Previous run still in progress")
        if not (agent.task_prompt or "").strip():
            raise _SkipRun("Agent has no task to run")

        manager = get_agent_manager()
        if not manager.ready:
            raise RuntimeError("Agents runtime not started")
        if clear_context:
            # Wipe prior transcript (same public id) and replay the stored task.
            await manager.rerun_task(agent_session_id)
        else:
            # Re-send the task into the existing conversation as a follow-up.
            await manager.send_message(agent_session_id, agent.task_prompt)

    async def _publish_agent(self, agent_session_id: int) -> None:
        from precursor.backend.models import AgentSession

        async with SessionLocal() as session:
            agent = await session.get(AgentSession, agent_session_id)
        await publish_agent_changed(
            agent_session_id=agent_session_id,
            topic_id=agent.topic_id if agent else None,
            chat_id=agent.chat_id if agent else None,
        )


_scheduler: Scheduler | None = None


def get_scheduler() -> Scheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler
