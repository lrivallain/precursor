"""Remove archived agent events that carry nothing to render.

Until streaming frames stopped being archived, every token of an agent's reply
and thinking was written to ``agent_events`` as its own row — with its text
dropped on the way — alongside byte counters, message-start markers and
textless messages and reasoning. One live install held 88.7k such rows out of
99.5k (16 MB of 23.7 MB of payload). Nothing renders them, the timeline skips
them on load, and they still counted against ``agent_event_max_per_session``,
crowding out real history.

New rows are no longer written (see ``is_content_free``); this sweep clears the
backlog. It deletes only rows that predicate matches, so every node a timeline
shows survives it, and it is idempotent.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, cast

from sqlalchemy import delete, func, or_, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import AgentEventRecord
from precursor.backend.services.agents.event_normalizer import (
    LIVE_ONLY_KINDS,
    is_content_free,
)
from precursor.backend.services.sweep_result import SweepResult

logger = logging.getLogger(__name__)

# Every kind a content-free row can have: the streaming frames, plus the two
# kinds that are empty when they have no text.
_CANDIDATE_KINDS = (*sorted(LIVE_ONLY_KINDS), "assistant_message", "reasoning")

# Rows loaded per batch, to bound memory on a large backlog.
_BATCH = 1000

# Ids bound per ``IN (…)``, under SQLite's host-parameter limit.
_CHUNK = 500


def _kind_filter() -> Any:
    """A cheap SQL pre-filter on the payload's kind.

    Payloads are written compact (``"kind":"…"``), but a row rewritten by the
    oversized-event recap is spaced (``"kind": "…"``), so match both. The exact
    decision is made on the decoded payload.
    """
    patterns = [f'%"kind":{sep}"{kind}"%' for kind in _CANDIDATE_KINDS for sep in ("", " ")]
    return or_(*(AgentEventRecord.payload.like(p) for p in patterns))


def _is_empty_payload(payload: str) -> bool:
    try:
        event = json.loads(payload)
    except ValueError:
        return False
    return isinstance(event, dict) and is_content_free(event.get("kind"), event.get("text"))


async def prune_empty_agent_events(
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] = SessionLocal,
    *,
    dry_run: bool = False,
) -> SweepResult:
    """Delete (or with ``dry_run`` only measure) content-free archived events."""
    ids: list[int] = []
    total = 0
    last_id = 0
    async with session_factory() as session:
        while True:
            batch = (
                await session.execute(
                    select(
                        AgentEventRecord.id,
                        AgentEventRecord.payload,
                        func.length(AgentEventRecord.payload),
                    )
                    .where(AgentEventRecord.id > last_id, _kind_filter())
                    .order_by(AgentEventRecord.id)
                    .limit(_BATCH)
                )
            ).all()
            if not batch:
                break
            last_id = int(batch[-1][0])
            for row_id, payload, size in batch:
                if _is_empty_payload(payload):
                    ids.append(int(row_id))
                    total += int(size or 0)

        if dry_run or not ids:
            return SweepResult(rows=len(ids), bytes=total)

        deleted = 0
        for start in range(0, len(ids), _CHUNK):
            result = await session.execute(
                delete(AgentEventRecord).where(AgentEventRecord.id.in_(ids[start : start + _CHUNK]))
            )
            deleted += int(cast("CursorResult[Any]", result).rowcount or 0)
        await session.commit()
    logger.info("Removed %d empty archived agent event(s) (~%d bytes)", deleted, total)
    return SweepResult(rows=deleted, bytes=total)
