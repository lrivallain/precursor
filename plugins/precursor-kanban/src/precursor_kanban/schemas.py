"""GitHub Projects v2 board schemas (read models + status-update payload)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from precursor.plugin_api import IssueLabel


class ProjectSummary(BaseModel):
    """A ProjectV2 available to the board.

    Either owned by the configured repository's owner, or added explicitly as an
    extra source in the plugin's settings — hence ``owner``, which is what tells
    two identically titled boards apart.
    """

    id: str
    number: int
    title: str
    url: str | None = None
    closed: bool = False
    short_description: str | None = None
    owner: str | None = None
    #: Where this board came from. ``repo`` is the implicit default (the
    #: configured repository's owner) and has no settings entry behind it;
    #: ``account`` and ``pinned`` were both added explicitly. The board's
    #: context menu needs this to know whether "stop tracking" is even
    #: meaningful, and how many boards it would take with it.
    source: Literal["repo", "account", "pinned"] = "repo"
    #: The settings entry that produced this board, exactly as the user typed
    #: it, so removing it is a plain array filter rather than a guess. ``None``
    #: for ``repo``, which no entry produced.
    source_ref: str | None = None
    #: Whether the user has hidden this board from the picker. Hidden boards are
    #: still returned: the picker is the only place to unhide one, so leaving
    #: them out would make hiding a one-way door.
    hidden: bool = False


class UnresolvedSource(BaseModel):
    """A configured source that currently yields no boards.

    Either it broke — renamed, revoked, made private — or the account genuinely
    has no open projects. Both are reported so the picker can show the entry and
    offer to remove it; otherwise a source that resolves to nothing would be
    invisible *and* undeletable.
    """

    #: The stored entry, verbatim, so removing it is an exact array filter.
    ref: str
    kind: Literal["account", "pinned"]


class ProjectListing(BaseModel):
    """Everything the picker needs: the boards, plus sources that produced none."""

    projects: list[ProjectSummary] = Field(default_factory=list)
    unresolved: list[UnresolvedSource] = Field(default_factory=list)


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
    labels: list[IssueLabel] = Field(default_factory=list)


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
