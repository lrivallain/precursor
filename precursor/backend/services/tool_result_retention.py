"""Age-limited retention for persisted TOOL message results and model thinking.

Large tool outputs persisted in ``messages.content`` accumulate over time (one
prod DB held ~8 MB of TOOL rows). When a retention window is configured, this
sweep replaces the ``content`` of TOOL rows older than the cutoff with a short
placeholder — **in place**. The row and its ``tool_calls`` JSON metadata are
kept intact so ``hydrate_history()`` (services/turn_engine.py) still pairs each
assistant tool-call turn with its TOOL rows and never drops a whole turn.

The same window clears the model's thinking (``messages.reasoning``) on older
assistant turns. It is display-only — never replayed to the model — so it is
dropped outright rather than replaced, and the reply simply loses its collapsed
"Thinking" area. Default retention is 0 (disabled / keep forever).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from precursor.backend.db import SessionLocal
from precursor.backend.models import Message, MessageRole
from precursor.backend.services.app_settings import resolve_tool_result_retention_days
from precursor.backend.services.sweep_result import SweepResult

logger = logging.getLogger(__name__)

# Short replacement text for pruned tool results. Defined once and reused in the
# WHERE clause so the sweep is idempotent (already-pruned rows are skipped).
PRUNED_PLACEHOLDER = "[Tool result pruned to save space]"

# Only prune rows whose content is longer than this floor — placeholders and
# already-small results aren't worth touching.
_MIN_CONTENT_LEN = 200


async def _measure(
    session: AsyncSession, column: Any, criteria: tuple[ColumnElement[bool], ...]
) -> tuple[int, int]:
    """Rows matching ``criteria`` and the total length of ``column`` across them."""
    measured = (
        await session.execute(
            select(func.count(Message.id), func.coalesce(func.sum(func.length(column)), 0)).where(
                *criteria
            )
        )
    ).one()
    return int(measured[0] or 0), int(measured[1] or 0)


async def prune_expired_tool_results(
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] = SessionLocal,
    *,
    dry_run: bool = False,
) -> SweepResult:
    """Placeholder expired TOOL results and clear expired thinking; report the effect.

    A no-op when retention is disabled (0 days). Otherwise truncates role=TOOL
    rows older than ``now - retention`` whose content exceeds a small floor and
    isn't already the placeholder, and nulls ``reasoning`` on older rows. With
    ``dry_run`` the rows are only measured, so the settings UI can preview a
    sweep before committing to it.
    """
    async with session_factory() as session:
        retention_days = await resolve_tool_result_retention_days(session)
        if retention_days <= 0:
            return SweepResult()
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        tool_criteria = (
            Message.role == MessageRole.TOOL,
            Message.created_at < cutoff,
            func.length(Message.content) > _MIN_CONTENT_LEN,
            Message.content != PRUNED_PLACEHOLDER,
        )
        thinking_criteria = (
            Message.reasoning.is_not(None),
            Message.created_at < cutoff,
        )
        tool_rows, tool_chars = await _measure(session, Message.content, tool_criteria)
        thinking_rows, thinking_chars = await _measure(
            session, Message.reasoning, thinking_criteria
        )
        # Each tool row keeps the placeholder, so only the excess is reclaimed.
        tool_freed = max(tool_chars - tool_rows * len(PRUNED_PLACEHOLDER), 0)
        freed = tool_freed + thinking_chars
        if dry_run or not (tool_rows or thinking_rows):
            return SweepResult(rows=tool_rows + thinking_rows, bytes=freed)

        pruned = cleared = 0
        if tool_rows:
            result = await session.execute(
                update(Message).where(*tool_criteria).values(content=PRUNED_PLACEHOLDER)
            )
            pruned = int(cast("CursorResult[Any]", result).rowcount or 0)
        if thinking_rows:
            result = await session.execute(
                update(Message).where(*thinking_criteria).values(reasoning=None)
            )
            cleared = int(cast("CursorResult[Any]", result).rowcount or 0)
        await session.commit()
        if pruned or cleared:
            logger.info(
                "Pruned %d expired tool result(s) and cleared %d model thinking "
                "block(s) older than %d day(s)",
                pruned,
                cleared,
                retention_days,
            )
        return SweepResult(rows=pruned + cleared, bytes=freed)
