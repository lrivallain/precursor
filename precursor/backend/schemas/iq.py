"""Precursor IQ schemas — ranked retrieval and cited answers.

Mirrored in ``frontend/src/lib/types.ts`` (``IQHit``, ``IQRetrieveResponse``,
``IQAskResponse``, ``IQStatus``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from precursor.backend.schemas.schedule import UtcDateTime

# The navigable entity a hit opens: palette section plus ``memory``.
IQSection = Literal["topics", "chats", "agents", "live", "memory"]

IQField = Literal[
    "title",
    "description",
    "message",
    "prompt",
    "answer",
    "transcript",
    "insight",
    "notes",
    "summary",
    "brief",
    "attachment",
    "memory",
]


class IQHit(BaseModel):
    """One ranked passage. ``id`` is its citation number (``[^id]``)."""

    id: int
    section: IQSection
    field: IQField
    source_kind: str
    # Row id of the navigable entity (topic/chat/agent/live session/memory).
    entity_id: int
    # Navigation handle the SPA understands: a slug, or an agent's public id.
    ref: str | None = None
    # Topics only: root-first slug path (``collection/parent/child``).
    path: str | None = None
    title: str
    snippet: str
    # The passage itself (trimmed), for grounding.
    excerpt: str
    role: str | None = None
    is_title: bool = False
    score: float
    updated_at: UtcDateTime | None = None
    url: str | None = None


class IQRetrieveResponse(BaseModel):
    query: str
    hits: list[IQHit]
    # Numbered excerpts with ``[^n]`` markers, ready to ground a model on.
    markdown: str
    # ``fts5`` / ``tsvector`` / ``like`` — which lexical engine answered.
    lexical_backend: str
    # Whether vector similarity took part (embeddings on and available).
    semantic: bool
    # Changes still queued for indexing when the query ran.
    pending: int


class IQAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    # Restrict grounding to these entity kinds (default: all).
    sections: list[IQSection] | None = None


class IQAskResponse(BaseModel):
    question: str
    answer: str
    model: str | None = None
    # Sources the answer actually cites, in citation order.
    citations: list[IQHit]
    # Every source the model was given.
    sources: list[IQHit]


class IQStatus(BaseModel):
    enabled: bool
    chunks: int
    pending: int
    sections: dict[str, int]
    lexical_backend: str
    embeddings_enabled: bool
    embeddings_available: bool
    embedding_model: str
    embedded: int
    reconciled_at: str | None = None
