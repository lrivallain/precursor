"""Re-apply the current payload caps to already-archived agent events.

The normaliser caps what it writes (``services/agents/event_normalizer``), but
rows archived before a cap existed — or under a looser one — keep their original
size forever. Retention doesn't help: an oversized payload is not necessarily
*old*, and it counts as one row against a per-session ceiling no matter how many
KB it holds. One prod DB carried 45 MB of such rows in a 126 MB database.

This sweep rewrites those payloads in place through the same cap functions the
normaliser uses, so the archive converges on the current policy without dropping
any event. The timeline keeps every node, every tool name and every status —
only the long tails of captured blobs are trimmed, and each trimmed value is
marked so it can't be mistaken for the original.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import AgentEventRecord
from precursor.backend.services.agents.event_normalizer import (
    SYSTEM_TEXT_CAP,
    TEXT_CAP,
    TOOL_RESULT_CAP,
    cap,
)
from precursor.backend.services.sweep_result import SweepResult

logger = logging.getLogger(__name__)

# Only inspect payloads that could plausibly exceed a cap. A fully compliant
# event tops out around ``TEXT_CAP`` plus a small envelope, so anything under
# this floor cannot be shrunk and isn't worth parsing.
_RECAP_FLOOR = 8_000

# Rows loaded (and rewritten) per batch, to bound memory on a large backlog.
_BATCH = 200


def recap_payload(payload: str) -> str | None:
    """Return a re-capped payload, or ``None`` when nothing would change.

    Operates on the raw JSON rather than the ``AgentEvent`` model so unknown or
    future fields survive the rewrite untouched.
    """
    try:
        event: Any = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(event, dict):
        return None

    changed = False
    text = event.get("text")
    if isinstance(text, str):
        limit = SYSTEM_TEXT_CAP if event.get("kind") == "SystemMessageData" else TEXT_CAP
        capped = cap(text, limit)
        if capped != text:
            event["text"] = capped
            changed = True

    data = event.get("data")
    if isinstance(data, dict):
        for key, value in data.items():
            # Ints (token counts) and bools (success/sandboxed) are meaningful
            # as-is; only captured strings carry the bulk.
            if not isinstance(value, str):
                continue
            capped = cap(value, TOOL_RESULT_CAP)
            if capped != value:
                data[key] = capped
                changed = True

    if not changed:
        return None
    return json.dumps(event, ensure_ascii=False)


async def recap_archived_events(
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] = SessionLocal,
    *,
    dry_run: bool = False,
) -> SweepResult:
    """Shrink over-cap archived payloads; report rows touched and bytes freed."""
    rows_touched = 0
    bytes_freed = 0
    last_id = 0

    while True:
        async with session_factory() as session:
            batch = (
                await session.execute(
                    select(AgentEventRecord.id, AgentEventRecord.payload)
                    .where(
                        AgentEventRecord.id > last_id,
                        func.length(AgentEventRecord.payload) > _RECAP_FLOOR,
                    )
                    .order_by(AgentEventRecord.id)
                    .limit(_BATCH)
                )
            ).all()
            if not batch:
                break
            last_id = int(batch[-1][0])

            updates: list[tuple[int, str]] = []
            for row_id, payload in batch:
                shrunk = recap_payload(payload)
                if shrunk is None or len(shrunk) >= len(payload):
                    continue
                rows_touched += 1
                bytes_freed += len(payload) - len(shrunk)
                updates.append((int(row_id), shrunk))

            if updates and not dry_run:
                for row_id, shrunk in updates:
                    await session.execute(
                        update(AgentEventRecord)
                        .where(AgentEventRecord.id == row_id)
                        .values(payload=shrunk)
                    )
                await session.commit()

    if rows_touched and not dry_run:
        logger.info(
            "Re-capped %d oversized archived agent event(s), freeing ~%d bytes",
            rows_touched,
            bytes_freed,
        )
    return SweepResult(rows=rows_touched, bytes=bytes_freed)
