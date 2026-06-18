"""Usage-statistics schemas — aggregated token consumption read models."""

from __future__ import annotations

from pydantic import BaseModel


class UsageBucket(BaseModel):
    """Token usage accumulated over a single time bucket.

    ``period`` is the bucket key: an ISO week (``YYYY-Www``), a month
    (``YYYY-MM``), a year (``YYYY``), or ``"all"`` for the grand total.
    """

    period: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    message_count: int = 0


class UsageStats(BaseModel):
    """Global token usage across every topic and chat.

    The per-period lists are ordered chronologically (oldest first).
    """

    totals: UsageBucket
    weekly: list[UsageBucket] = []
    monthly: list[UsageBucket] = []
    yearly: list[UsageBucket] = []
