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
