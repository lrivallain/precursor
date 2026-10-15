"""Conversation artifact schemas — workspace files attached to a topic or chat."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ArtifactRead(BaseModel):
    id: int
    workspace_id: int
    workspace_slug: str
    workspace_name: str
    path: str
    source: str
    # False once the file was renamed or deleted; the link is kept, not tracked.
    exists: bool = True
    created_at: datetime
    updated_at: datetime


class ArtifactCreate(BaseModel):
    workspace_id: int
    path: str = Field(min_length=1)
    # ``opened``: the discussion was opened on the file in the workspace.
    source: Literal["manual", "opened"] = "manual"


class WorkspaceTurnContext(BaseModel):
    """Where a turn is being held: the workspace (and file) the user is viewing."""

    workspace_id: int
    path: str | None = None


class DiscussionItem(BaseModel):
    """A topic or chat that attaches files of one workspace."""

    kind: Literal["chat", "topic"]
    id: int
    title: str
    slug: str
    # Topics only: the immutable permalink id, used in the workspace URL.
    public_id: str | None = None
    updated_at: datetime
    # The workspace files this discussion attaches.
    paths: list[str]
    # True when one of them is the file the caller asked about.
    has_file: bool = False
