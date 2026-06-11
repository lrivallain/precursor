"""Topic-related Pydantic schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TopicBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    parent_id: int | None = None
    github_repo: str | None = None
    github_issue_number: int | None = None


class TopicCreate(TopicBase):
    pass


class TopicUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    parent_id: int | None = None
    github_repo: str | None = None
    github_issue_number: int | None = None


class TopicRead(TopicBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class TopicNode(TopicRead):
    """Topic with nested children, used by the sidebar tree."""

    children: list["TopicNode"] = Field(default_factory=list)


TopicNode.model_rebuild()
