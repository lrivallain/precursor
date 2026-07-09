"""Topic-related Pydantic schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from precursor.backend.schemas.schedule import ScheduleSummary


class TopicBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    parent_id: int | None = None
    github_repo: str | None = None
    github_issue_number: int | None = None
    pinned: bool = False
    role_id: int | None = None


class TopicCreate(TopicBase):
    # Optional explicit slug. If omitted, the server derives one from the title.
    slug: str | None = Field(default=None, min_length=1, max_length=255)
    # When true, open a GitHub issue for the new topic and link it back. The
    # issue title is prefixed with the ancestor topic chain and its body is the
    # description. Not a Topic column — the router consumes it during creation.
    create_linked_issue: bool = False


class TopicUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    parent_id: int | None = None
    github_repo: str | None = None
    github_issue_number: int | None = None
    pinned: bool | None = None
    role_id: int | None = None
    # When present, the router normalizes and uniquifies it before storing.
    slug: str | None = Field(default=None, min_length=1, max_length=255)


class TopicRead(TopicBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    kind: str = "standard"
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    # Recurrence summary when the topic runs on a schedule (null otherwise).
    # Eager-loaded (selectin) so any topic read carries it.
    schedule: ScheduleSummary | None = None


class TopicNode(TopicRead):
    """Topic with nested children, used by the sidebar tree."""

    children: list[TopicNode] = Field(default_factory=list)
    unread_count: int = 0


TopicNode.model_rebuild()
