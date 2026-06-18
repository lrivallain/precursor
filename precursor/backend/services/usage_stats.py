"""Global token-usage aggregation across all topics and chats."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import UsageRecord
from precursor.backend.schemas.stats import UsageBucket, UsageStats


async def record_usage(
    session: AsyncSession,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int | None = None,
    source: str = "chat",
    model: str | None = None,
    topic_id: int | None = None,
    chat_id: int | None = None,
) -> None:
    """Append one metered LLM round-trip to the usage ledger.

    Caller owns the commit. No-ops when the round reported zero tokens so
    providers that don't surface usage don't pollute the ledger.
    """
    if not prompt_tokens and not completion_tokens:
        return
    session.add(
        UsageRecord(
            source=source,
            model=model,
            topic_id=topic_id,
            chat_id=chat_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens
            if total_tokens is not None
            else prompt_tokens + completion_tokens,
        )
    )


def _bucket_keys(dt: datetime) -> tuple[str, str, str]:
    """Return (week, month, year) bucket keys for a timestamp.

    Naive timestamps are assumed to be UTC so buckets stay stable regardless
    of how the database returns the value.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    iso_year, iso_week, _ = dt.isocalendar()
    week = f"{iso_year:04d}-W{iso_week:02d}"
    month = f"{dt.year:04d}-{dt.month:02d}"
    year = f"{dt.year:04d}"
    return week, month, year


def _sorted_buckets(acc: dict[str, dict[str, int]]) -> list[UsageBucket]:
    return [
        UsageBucket(
            period=period,
            prompt_tokens=vals["prompt"],
            completion_tokens=vals["completion"],
            total_tokens=vals["prompt"] + vals["completion"],
            message_count=vals["count"],
        )
        for period, vals in sorted(acc.items())
    ]


async def compute_usage_stats(session: AsyncSession) -> UsageStats:
    """Accumulate token usage from the usage ledger.

    The ledger records every metered LLM round-trip — chat turns, tool rounds,
    and utility commands (``/notes``, ``/gh-create``, …) — so usage is truly
    global. ``message_count`` here counts ledger entries (round-trips), not
    conversation messages.
    """
    result = await session.execute(
        select(
            UsageRecord.created_at,
            UsageRecord.prompt_tokens,
            UsageRecord.completion_tokens,
        )
    )

    weekly: dict[str, dict[str, int]] = defaultdict(
        lambda: {"prompt": 0, "completion": 0, "count": 0}
    )
    monthly: dict[str, dict[str, int]] = defaultdict(
        lambda: {"prompt": 0, "completion": 0, "count": 0}
    )
    yearly: dict[str, dict[str, int]] = defaultdict(
        lambda: {"prompt": 0, "completion": 0, "count": 0}
    )
    total_prompt = 0
    total_completion = 0
    total_count = 0

    for created_at, prompt_tokens, completion_tokens in result.all():
        prompt = prompt_tokens or 0
        completion = completion_tokens or 0
        week, month, year = _bucket_keys(created_at)
        for acc, key in ((weekly, week), (monthly, month), (yearly, year)):
            acc[key]["prompt"] += prompt
            acc[key]["completion"] += completion
            acc[key]["count"] += 1
        total_prompt += prompt
        total_completion += completion
        total_count += 1

    return UsageStats(
        totals=UsageBucket(
            period="all",
            prompt_tokens=total_prompt,
            completion_tokens=total_completion,
            total_tokens=total_prompt + total_completion,
            message_count=total_count,
        ),
        weekly=_sorted_buckets(weekly),
        monthly=_sorted_buckets(monthly),
        yearly=_sorted_buckets(yearly),
    )
