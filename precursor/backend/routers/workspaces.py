"""Workspace endpoints — Git-backed Markdown authoring with AI help.

Workspaces are working copies of GitHub repositories. The browser/editor
operate on files relative to the workspace's (optional) subdir; git sync
operates on the repository root. Chat is ephemeral assist over the active
file's content.
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from precursor.backend.config import get_settings
from precursor.backend.db import get_session
from precursor.backend.models import Workspace
from precursor.backend.schemas import (
    CommitRequest,
    FileContent,
    FileCreate,
    FileDiff,
    FileNode,
    FileRename,
    FileWrite,
    FolderCreate,
    GitActionResult,
    GitStatus,
    LocalPath,
    WorkspaceCreate,
    WorkspaceRead,
    WorkspaceUpdate,
)
from precursor.backend.schemas.workspace import WorkspaceChatRequest
from precursor.backend.services import workspace_fs as fs
from precursor.backend.services import workspace_git as git
from precursor.backend.services.conversation_turn import resolve_turn_settings
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.slugs import slugify
from precursor.backend.services.workspace_chat import (
    build_workspace_system_prompt,
    run_workspace_stream,
    workspace_history,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


def workspace_root(ws: Workspace) -> Path:
    """Repository working-copy root on disk."""
    return Path(get_settings().workspaces_dir) / ws.slug


def browse_root(ws: Workspace) -> Path:
    """File-browser root (repo root, or the configured subdir within it)."""
    root = workspace_root(ws)
    if ws.subdir:
        return root / ws.subdir.strip("/")
    return root


async def get_workspace_by_slug(slug: str, session: AsyncSession) -> Workspace | None:
    return (
        await session.execute(select(Workspace).where(Workspace.slug == slug))
    ).scalar_one_or_none()


async def _get_workspace(workspace_id: int, session: AsyncSession) -> Workspace:
    ws = await session.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    return ws


async def _get_git_workspace(workspace_id: int, session: AsyncSession) -> Workspace:
    """Fetch a workspace and reject git operations on local (non-git) ones."""
    ws = await _get_workspace(workspace_id, session)
    if ws.kind != "git":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This is a local workspace — git operations are not available.",
        )
    return ws


async def _allocate_slug(session: AsyncSession, base: str) -> str:
    base = base or "workspace"
    candidate = base
    n = 2
    while True:
        existing = (
            await session.execute(select(Workspace.id).where(Workspace.slug == candidate))
        ).first()
        if existing is None:
            return candidate
        candidate = f"{base}-{n}"
        n += 1


# --------------------------------------------------------------------------
# Workspace CRUD
# --------------------------------------------------------------------------


@router.get("", response_model=list[WorkspaceRead])
async def list_workspaces(
    session: AsyncSession = Depends(get_session),
) -> list[Workspace]:
    result = await session.execute(select(Workspace).order_by(Workspace.created_at.desc()))
    return list(result.scalars().all())


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    session: AsyncSession = Depends(get_session),
) -> Workspace:
    is_local = payload.kind == "local"
    if not is_local and not (payload.repo_url or "").strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A repository URL is required for a git workspace.",
        )
    if not is_local and not git.git_available():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "git is not installed on the server — install it to use git workspaces.",
        )

    slug = slugify(payload.slug or payload.name)
    slug = await _allocate_slug(session, slug)

    ws = Workspace(
        name=payload.name,
        slug=slug,
        kind="local" if is_local else "git",
        repo_url=None if is_local else (payload.repo_url or "").strip(),
        branch=payload.branch.strip() or "main",
        subdir=(payload.subdir or "").strip() or None,
    )
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    dest = workspace_root(ws)
    if is_local:
        # A local workspace is just an empty folder we own under workspaces_dir.
        dest.mkdir(parents=True, exist_ok=True)
    else:
        # Clone inline. On failure, keep the row so the user can retry via re-clone.
        token = await resolve_github_token(session)
        try:
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            await git.clone(ws.repo_url or "", dest, ws.branch, token)
        except git.GitError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    ws.cloned_at = datetime.now(UTC)
    ws.last_synced_at = ws.cloned_at
    await session.commit()
    await session.refresh(ws)
    return ws


@router.patch("/{workspace_id}", response_model=WorkspaceRead)
async def update_workspace(
    workspace_id: int,
    payload: WorkspaceUpdate,
    session: AsyncSession = Depends(get_session),
) -> Workspace:
    """Update mutable workspace fields (currently the assigned Assistant Role)."""
    ws = await _get_workspace(workspace_id, session)
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(ws, key, value)
    await session.commit()
    await session.refresh(ws)
    return ws


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(workspace_id: int, session: AsyncSession = Depends(get_session)) -> None:
    ws = await _get_workspace(workspace_id, session)
    dest = workspace_root(ws)
    await session.delete(ws)
    await session.commit()
    # Remove the working copy from disk after the row is gone.
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)


@router.get("/{workspace_id}/local-path", response_model=LocalPath)
async def workspace_local_path(
    workspace_id: int, session: AsyncSession = Depends(get_session)
) -> LocalPath:
    """Absolute path of the workspace's working copy, for use in a terminal/editor."""
    ws = await _get_workspace(workspace_id, session)
    return LocalPath(path=str(workspace_root(ws).resolve()))


# --------------------------------------------------------------------------
# File browser / editor
# --------------------------------------------------------------------------


@router.get("/{workspace_id}/files", response_model=list[FileNode])
async def list_files(
    workspace_id: int, session: AsyncSession = Depends(get_session)
) -> list[FileNode]:
    ws = await _get_workspace(workspace_id, session)
    return fs.list_tree(browse_root(ws))


@router.get("/{workspace_id}/file", response_model=FileContent)
async def read_file(
    workspace_id: int,
    path: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> FileContent:
    ws = await _get_workspace(workspace_id, session)
    try:
        content = fs.read_text(browse_root(ws), path)
    except fs.UnsafePathError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found") from exc
    except UnicodeDecodeError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Not a text file") from exc
    return FileContent(path=path, content=content)


@router.put("/{workspace_id}/file", response_model=FileContent)
async def write_file(
    workspace_id: int,
    payload: FileWrite,
    path: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> FileContent:
    ws = await _get_workspace(workspace_id, session)
    try:
        fs.write_text(browse_root(ws), path, payload.content)
    except fs.UnsafePathError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return FileContent(path=path, content=payload.content)


@router.post(
    "/{workspace_id}/file",
    response_model=FileContent,
    status_code=status.HTTP_201_CREATED,
)
async def create_file_endpoint(
    workspace_id: int,
    payload: FileCreate,
    session: AsyncSession = Depends(get_session),
) -> FileContent:
    ws = await _get_workspace(workspace_id, session)
    try:
        fs.create_file(browse_root(ws), payload.path, payload.content)
    except fs.UnsafePathError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "File already exists") from exc
    return FileContent(path=payload.path, content=payload.content)


@router.post(
    "/{workspace_id}/folder",
    response_model=FileNode,
    status_code=status.HTTP_201_CREATED,
)
async def create_folder_endpoint(
    workspace_id: int,
    payload: FolderCreate,
    session: AsyncSession = Depends(get_session),
) -> FileNode:
    ws = await _get_workspace(workspace_id, session)
    path = payload.path.strip().strip("/")
    try:
        fs.create_dir(browse_root(ws), path)
    except fs.UnsafePathError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Folder already exists") from exc
    return FileNode(path=path, name=path.rsplit("/", 1)[-1], type="dir")


@router.delete("/{workspace_id}/file", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file_endpoint(
    workspace_id: int,
    path: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> None:
    ws = await _get_workspace(workspace_id, session)
    try:
        fs.delete_file(browse_root(ws), path)
    except fs.UnsafePathError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found") from exc


@router.post("/{workspace_id}/rename", response_model=FileNode)
async def rename_endpoint(
    workspace_id: int,
    payload: FileRename,
    session: AsyncSession = Depends(get_session),
) -> FileNode:
    ws = await _get_workspace(workspace_id, session)
    src = payload.path.strip().strip("/")
    dst = payload.new_path.strip().strip("/")
    root = browse_root(ws)
    try:
        fs.rename(root, src, dst)
        node_type = "dir" if fs.safe_join(root, dst).is_dir() else "file"
    except fs.UnsafePathError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found") from exc
    except FileExistsError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A file or folder already exists at the destination"
        ) from exc
    return FileNode(path=dst, name=dst.rsplit("/", 1)[-1], type=node_type)


# --------------------------------------------------------------------------
# Git sync
# --------------------------------------------------------------------------


@router.get("/{workspace_id}/git/status", response_model=GitStatus)
async def git_status(workspace_id: int, session: AsyncSession = Depends(get_session)) -> GitStatus:
    ws = await _get_git_workspace(workspace_id, session)
    try:
        return await git.status(workspace_root(ws))
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/{workspace_id}/git/pull", response_model=GitActionResult)
async def git_pull(
    workspace_id: int, session: AsyncSession = Depends(get_session)
) -> GitActionResult:
    ws = await _get_git_workspace(workspace_id, session)
    token = await resolve_github_token(session)
    root = workspace_root(ws)
    try:
        ok, detail = await git.pull(root, ws.branch, token)
        st = await git.status(root)
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if ok:
        ws.last_synced_at = datetime.now(UTC)
        await session.commit()
    return GitActionResult(
        ok=ok,
        detail=detail,
        needs_manual_merge=not ok,
        local_path=str(root),
        status=st,
    )


@router.post("/{workspace_id}/git/commit-push", response_model=GitActionResult)
async def git_commit_push(
    workspace_id: int,
    payload: CommitRequest,
    session: AsyncSession = Depends(get_session),
) -> GitActionResult:
    ws = await _get_git_workspace(workspace_id, session)
    token = await resolve_github_token(session)
    root = workspace_root(ws)
    try:
        if payload.paths is not None:
            committed, commit_detail = await git.commit_paths(root, payload.message, payload.paths)
        else:
            committed, commit_detail = await git.commit_all(root, payload.message)
        if not committed:
            st = await git.status(root)
            return GitActionResult(ok=False, detail=commit_detail, local_path=str(root), status=st)
        pushed, push_detail = await git.push(root, ws.branch, token)
        st = await git.status(root)
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if pushed:
        ws.last_synced_at = datetime.now(UTC)
        await session.commit()
    return GitActionResult(
        ok=pushed,
        detail=push_detail if pushed else f"Committed locally but push failed: {push_detail}",
        needs_manual_merge=not pushed,
        local_path=str(root),
        status=st,
    )


@router.post("/{workspace_id}/git/discard", response_model=GitStatus)
async def git_discard(
    workspace_id: int,
    path: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> GitStatus:
    ws = await _get_git_workspace(workspace_id, session)
    root = workspace_root(ws)
    try:
        await git.discard(root, path)
        return await git.status(root)
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/{workspace_id}/git/diff", response_model=FileDiff)
async def git_diff(
    workspace_id: int,
    path: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> FileDiff:
    ws = await _get_git_workspace(workspace_id, session)
    root = workspace_root(ws)
    try:
        diff, binary = await git.diff_file(root, path)
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return FileDiff(path=path, diff=diff, binary=binary)


# --------------------------------------------------------------------------
# Chat — ephemeral authoring assistant bound to the active file
# --------------------------------------------------------------------------


@router.post("/{workspace_id}/chat/stream")
async def chat_stream(
    workspace_id: int,
    payload: WorkspaceChatRequest,
    session: AsyncSession = Depends(get_session),
) -> EventSourceResponse:
    ws = await _get_workspace(workspace_id, session)
    system_prompt = await build_workspace_system_prompt(session, ws, browse_root(ws), payload.path)
    settings = await resolve_turn_settings(session, model_override=payload.model)
    return EventSourceResponse(
        run_workspace_stream(
            system_prompt=system_prompt,
            history=workspace_history(payload),
            settings=settings,
        )
    )
