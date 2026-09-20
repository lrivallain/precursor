"""Schemas for the editable topic summary panel."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SummaryHunkRead(BaseModel):
    """One reviewable change between the live summary and a proposal."""

    index: int
    removed: list[str]
    added: list[str]


class SummarySuggestionRead(BaseModel):
    """A model proposal parked for per-change review."""

    content: str
    model: str | None = None
    generated_at: datetime | None = None
    hunks: list[SummaryHunkRead] = Field(default_factory=list)


class TopicSummaryRead(BaseModel):
    revision: str
    content: str
    visible: bool
    user_edited: bool
    model: str | None = None
    generated_at: datetime | None = None
    updated_at: datetime | None = None
    suggestion: SummarySuggestionRead | None = None


class TopicSummarySave(BaseModel):
    """A manual edit. Saving marks the summary as user-owned."""

    content: str
    visible: bool | None = None
    revision: str | None = None


class TopicSummaryVisibility(BaseModel):
    visible: bool


class TopicSummaryGenerate(BaseModel):
    """Refresh request; ``instruction`` is the text after the slash command."""

    instruction: str | None = None


class TopicSummaryResolve(BaseModel):
    """Per-change verdict on a pending suggestion (indices to accept)."""

    revision: str
    accepted: list[int] = Field(default_factory=list)


class TopicSummaryItem(BaseModel):
    """Append one action (`/todo-summary`) or fact (`/important-summary`)."""

    kind: Literal["todo", "important"]
    text: str = Field(min_length=1)
