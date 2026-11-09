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

from sqlalchemy import func

from precursor.backend.db import SessionLocal
from precursor.backend.models import Message, MessageRole
from precursor.backend.services.app_settings import resolve_tool_result_retention_days
from precursor.backend.services.retention_sweep import SessionFactory, cutoff, replace_column
from precursor.backend.services.sweep_result import SweepResult

logger = logging.getLogger(__name__)

# Short replacement text for pruned tool results. Defined once and reused in the
# WHERE clause so the sweep is idempotent (already-pruned rows are skipped).
PRUNED_PLACEHOLDER = "[Tool result pruned to save space]"

# Only prune rows whose content is longer than this floor — placeholders and
# already-small results aren't worth touching.
_MIN_CONTENT_LEN = 200


async def prune_expired_tool_results(
    session_factory: SessionFactory = SessionLocal,
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
        expired = Message.created_at < cutoff(retention_days)
        tool = await replace_column(
            session,
            Message.content,
            (
                Message.role == MessageRole.TOOL,
                expired,
                func.length(Message.content) > _MIN_CONTENT_LEN,
                Message.content != PRUNED_PLACEHOLDER,
            ),
            replacement=PRUNED_PLACEHOLDER,
            dry_run=dry_run,
        )
        thinking = await replace_column(
            session,
            Message.reasoning,
            (Message.reasoning.is_not(None), expired),
            replacement=None,
            dry_run=dry_run,
        )
        if dry_run:
            return tool + thinking
        await session.commit()
        if tool.rows or thinking.rows:
            logger.info(
                "Pruned %d expired tool result(s) and cleared %d model thinking "
                "block(s) older than %d day(s)",
                tool.rows,
                thinking.rows,
                retention_days,
            )
        return tool + thinking
