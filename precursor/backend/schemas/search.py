"""Global content-search schemas — the ⌘K palette's cross-entity lookup.

A flat list of hits spanning topics, chats, agents (prompts + final answers
only) and live sessions. Each hit self-describes the section it belongs to (for
the palette's section colour/icon) and which field matched (``field`` +
``is_title``) so the UI can prioritise title hits and badge the rest.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from precursor.backend.schemas.schedule import UtcDateTime

# The palette section a hit belongs to — drives its colour + icon in the UI.
SearchSection = Literal["topics", "chats", "agents", "live"]

# Which field on the entity produced the match. ``title`` (and the equivalent
# top-level ``description``) rank first; the rest are body/discussion hits.
SearchField = Literal[
    "title",
    "description",
    "message",
    "prompt",
    "answer",
    "transcript",
    "insight",
    "notes",
    "summary",
]


class SearchResult(BaseModel):
    """A single match, ready to render + navigate to.

    ``ref`` is the navigation handle the SPA already understands: a topic /
    chat / live slug, or an agent's public ``copilot_session_id``.
    """

    section: SearchSection
    field: SearchField
    # True for title/name hits — the palette floats these above body hits.
    is_title: bool
    # Container id (topic/chat/agent/meeting-session row id).
    entity_id: int
    # Navigation handle (slug or agent uuid). May be null for legacy agent rows
    # without a public id yet, in which case the UI falls back to the id.
    ref: str | None = None
    # The container's display title, always shown as the result's heading.
    title: str
    # The matched text window (the title itself for title hits; a snippet around
    # the match for body hits).
    snippet: str
    # Message role for message hits ("user" / "assistant"), else null.
    role: str | None = None
    updated_at: UtcDateTime | None = None


class SearchResponse(BaseModel):
    """Search hits for ``query``, already sorted title-first then by recency."""

    query: str
    results: list[SearchResult] = []
