"""Age-limited retention for persisted TOOL message results.

Large tool outputs persisted in ``messages.content`` accumulate over time (one
prod DB held ~8 MB of TOOL rows). When a retention window is configured, this
sweep replaces the ``content`` of TOOL rows older than the cutoff with a short
placeholder — **in place**. The row and its ``tool_calls`` JSON metadata are
kept intact so ``hydrate_history()`` (services/turn_engine.py) still pairs each
assistant tool-call turn with its TOOL rows by ``tool_call_id`` and never drops
a whole turn. Default retention is 0 (disabled / keep forever).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import func, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import Message, MessageRole
from precursor.backend.services.app_settings import resolve_tool_result_retention_days

logger = logging.getLogger(__name__)

# Short replacement text for pruned tool results. Defined once and reused in the
# WHERE clause so the sweep is idempotent (already-pruned rows are skipped).
PRUNED_PLACEHOLDER = "[Tool result pruned to save space]"

# Only prune rows whose content is longer than this floor — placeholders and
# already-small results aren't worth touching.
_MIN_CONTENT_LEN = 200


async def prune_expired_tool_results(
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] = SessionLocal,
) -> int:
    """Replace content of expired TOOL rows with a placeholder; return the count.

    A no-op (returns 0) when retention is disabled (0 days). Otherwise truncates
    role=TOOL rows older than ``now - retention`` whose content exceeds a small
    floor and isn't already the placeholder.
    """
    async with session_factory() as session:
        retention_days = await resolve_tool_result_retention_days(session)
        if retention_days <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        result = await session.execute(
            update(Message)
            .where(
                Message.role == MessageRole.TOOL,
                Message.created_at < cutoff,
                func.length(Message.content) > _MIN_CONTENT_LEN,
                Message.content != PRUNED_PLACEHOLDER,
            )
            .values(content=PRUNED_PLACEHOLDER)
        )
        await session.commit()
        count = int(cast("CursorResult[Any]", result).rowcount or 0)
        if count:
            logger.info(
                "Pruned %d expired tool result(s) older than %d day(s)",
                count,
                retention_days,
            )
        return count
