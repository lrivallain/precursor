"""GitHub issue read models shared by core and plugins.

These describe a single issue/PR — its labels, comments and the Precursor topic
linked to it. The Projects v2 *board* models used to live here; they now ship
with the ``precursor-kanban`` plugin.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class IssueLabel(BaseModel):
    name: str
    color: str


class IssueComment(BaseModel):
    id: int
    user: str
    body: str
    created_at: str | None = None
    updated_at: str


class IssueDetail(BaseModel):
    """Full issue/PR view: metadata, body, labels and comments."""

    number: int
    title: str
    state: str
    url: str | None = None
    body: str = ""
    labels: list[IssueLabel] = Field(default_factory=list)
    updated_at: str | None = None
    comments: list[IssueComment] = Field(default_factory=list)
    # The linked Precursor topic, when a topic points at this issue/repo.
    linked_topic_id: int | None = None
    linked_topic_title: str | None = None
