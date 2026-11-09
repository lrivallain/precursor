"""Collection-related Pydantic schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from precursor.backend.models.collection import DEFAULT_COLLECTION_ACCENT, CollectionAccent


class CollectionBase(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = None
    # `owner/repo`; null/empty falls back to the global GitHub repo setting.
    github_repo: str | None = Field(default=None, max_length=255)
    accent: CollectionAccent = DEFAULT_COLLECTION_ACCENT
    icon: str | None = Field(default=None, max_length=64)
    # Default Assistant Role for new topics in this collection; null = built-in
    # default role.
    default_role: str | None = None


class CollectionCreate(CollectionBase):
    slug: str | None = Field(default=None, min_length=1, max_length=255)


class CollectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = None
    github_repo: str | None = Field(default=None, max_length=255)
    accent: CollectionAccent | None = None
    icon: str | None = Field(default=None, max_length=64)
    slug: str | None = Field(default=None, min_length=1, max_length=255)
    # Send explicit null to clear the collection's default role; omit to leave
    # it unchanged (relies on `exclude_unset=True` in the router).
    default_role: str | None = None


class CollectionRead(CollectionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    is_default: bool = False
    created_at: datetime
    updated_at: datetime
    # Number of non-archived topics currently in this collection.
    topic_count: int = 0
