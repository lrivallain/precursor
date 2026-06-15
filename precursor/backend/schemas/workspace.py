"""Workspace Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # "git" requires repo_url; "local" creates an empty on-disk folder.
    kind: Literal["git", "local"] = "git"
    repo_url: str | None = Field(default=None, max_length=1024)
    branch: str = Field(default="main", min_length=1, max_length=255)
    subdir: str | None = None
    # Optional explicit slug; derived from the name when omitted.
    slug: str | None = Field(default=None, min_length=1, max_length=255)


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    kind: str = "git"
    repo_url: str | None = None
    branch: str
    subdir: str | None = None
    cloned_at: datetime | None = None
    last_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class FileNode(BaseModel):
    """A file or directory in the working tree (relative to the workspace root)."""

    path: str
    name: str
    type: Literal["file", "dir"]


class FileContent(BaseModel):
    path: str
    content: str


class FileWrite(BaseModel):
    content: str


class FileCreate(BaseModel):
    path: str = Field(min_length=1)
    content: str = ""


class FolderCreate(BaseModel):
    path: str = Field(min_length=1)


class CommitRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    # When provided, only these paths are staged and committed; otherwise all
    # changes are committed (backward-compatible default).
    paths: list[str] | None = None


class FileDiff(BaseModel):
    path: str
    diff: str
    binary: bool = False


class LocalPath(BaseModel):
    # Absolute filesystem path of the workspace's working copy on the server.
    path: str


class GitFileStatus(BaseModel):
    path: str
    # Two-letter porcelain code, e.g. " M", "??", "A ".
    code: str


class GitStatus(BaseModel):
    branch: str
    # Commits ahead/behind the upstream branch (None when no upstream).
    ahead: int | None = None
    behind: int | None = None
    # True when there are uncommitted changes in the working tree.
    dirty: bool = False
    files: list[GitFileStatus] = Field(default_factory=list)


class GitActionResult(BaseModel):
    ok: bool
    # Human-readable summary or the captured git stderr/stdout on failure.
    detail: str = ""
    # When a fast-forward pull failed because of divergence/conflicts, this is
    # set so the UI can show the "resolve with the git CLI" help.
    needs_manual_merge: bool = False
    local_path: str | None = None
    status: GitStatus | None = None


class WorkspaceChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class WorkspaceChatRequest(BaseModel):
    # Latest user turn.
    content: str = Field(min_length=1)
    # Prior turns kept client-side (workspace chat is ephemeral, not stored).
    history: list[WorkspaceChatMessage] = Field(default_factory=list)
    # File the user is authoring; its content is injected as context.
    path: str | None = None
    model: str | None = None
    # When set (skill invocation), the LLM receives this as the last user turn
    # instead of `content`; the UI still shows the literal `content`.
    prompt_override: str | None = None
