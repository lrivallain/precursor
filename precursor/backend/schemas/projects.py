"""GitHub Projects v2 board schemas (read models + status-update payload)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ProjectSummary(BaseModel):
    """A ProjectV2 linked to the configured repository."""

    id: str
    number: int
    title: str
    url: str | None = None
    closed: bool = False
    short_description: str | None = None


class ProjectLabel(BaseModel):
    name: str
    color: str


class ProjectColumn(BaseModel):
    """A board column, derived from a Status single-select option."""

    id: str
    name: str


class ProjectStatusField(BaseModel):
    """The project's Status single-select field, driving the columns."""

    id: str
    name: str
    options: list[ProjectColumn] = Field(default_factory=list)


class ProjectCard(BaseModel):
    """An issue/PR item on the board."""

    id: str  # ProjectV2 item id (used for mutations)
    type: Literal["issue", "pull_request"]
    number: int | None = None
    title: str
    url: str | None = None
    state: str | None = None
    # ``owner/name`` of the item's source repo — ProjectsV2 can span repos, so
    # this drives the issue-preview fetch and topic linking rather than assuming
    # the configured repo.
    repo: str | None = None
    status_option_id: str | None = None
    status_name: str | None = None
    labels: list[ProjectLabel] = Field(default_factory=list)


class ProjectBoard(BaseModel):
    id: str
    title: str
    url: str | None = None
    status_field: ProjectStatusField | None = None
    items: list[ProjectCard] = Field(default_factory=list)


class ItemStatusUpdate(BaseModel):
    """Move a card to a different Status option."""

    field_id: str = Field(min_length=1)
    option_id: str = Field(min_length=1)


class ItemStatusResult(BaseModel):
    item_id: str
    option_id: str


class IssueComment(BaseModel):
    id: int
    user: str
    body: str
    updated_at: str


class IssueDetail(BaseModel):
    """Full issue/PR view for the kanban card preview."""

    number: int
    title: str
    state: str
    url: str | None = None
    body: str = ""
    labels: list[ProjectLabel] = Field(default_factory=list)
    updated_at: str | None = None
    comments: list[IssueComment] = Field(default_factory=list)
    # The linked Precursor topic, when a topic points at this issue/repo.
    linked_topic_id: int | None = None
    linked_topic_title: str | None = None
