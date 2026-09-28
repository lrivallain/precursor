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

from precursor.backend.config import DEFINITIONS_WORKSPACE_SLUG, get_settings
from precursor.backend.db import get_session
from precursor.backend.models import Workspace
from precursor.backend.schemas import (
    BranchRequest,
    CommitRequest,
    FileContent,
    FileCreate,
    FileDiff,
    FileNode,
    FileRename,
    FileVersions,
    FileWrite,
    FolderCreate,
    GitActionResult,
    GitBranches,
    GitCommitDetail,
    GitConflict,
    GitLog,
    GitStatus,
    LocalPath,
    ResolveRequest,
    WorkspaceCreate,
    WorkspaceRead,
    WorkspaceUpdate,
)
from precursor.backend.schemas.workspace import WorkspaceChatRequest
from precursor.backend.services import workspace_fs as fs
from precursor.backend.services import workspace_git as git
from precursor.backend.services.conversation_turn import resolve_turn_settings
from precursor.backend.services.definitions import anchors as definition_anchors
from precursor.backend.services.definitions import home as definitions_home
from precursor.backend.services.definitions import overlay as definition_overlay
from precursor.backend.services.definitions.loader import kind_for
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.slugs import slugify
from precursor.backend.services.workspace_chat import (
    build_workspace_system_prompt,
    run_workspace_stream,
    workspace_history,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


def _holds_definitions(root: Path) -> bool:
    defs = Path(get_settings().definitions_dir).resolve()
    here = root.resolve()
    return defs == here or here in defs.parents or defs in here.parents


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
        # The built-in definitions workspace's slug is kept for it, even while
        # it doesn't exist yet.
        if existing is None and candidate != DEFINITIONS_WORKSPACE_SLUG:
            return candidate
        candidate = f"{base}-{n}"
        n += 1


# --------------------------------------------------------------------------
# Workspace CRUD
# --------------------------------------------------------------------------


@router.get("", response_model=list[WorkspaceRead])
async def list_workspaces(
    session: AsyncSession = Depends(get_session),
) -> list[WorkspaceRead]:
    result = await session.execute(select(Workspace).order_by(Workspace.created_at.desc()))
    reads = []
    for ws in result.scalars().all():
        read = WorkspaceRead.model_validate(ws)
        read.hosts_definitions = definitions_home.holds_definitions(workspace_root(ws))
        reads.append(read)
    # The definitions workspace leads the list: it's the one every install has.
    return sorted(reads, key=lambda r: not r.hosts_definitions)


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
    if definitions_home.has_definition_files(dest):
        # Its folder would go with it — every agent and workflow declared there.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This workspace holds your agent and workflow definition files; it can't be removed.",
        )
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
    if kind_for(Path(path).name) is not None:
        # A definition file saved here should apply now, not a second later.
        definition_overlay.invalidate()
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


async def _checked_out_branch(ws: Workspace, root: Path, session: AsyncSession) -> str:
    """The branch to fetch/pull/push: the one checked out, whoever switched to it.

    ``Workspace.branch`` follows it, so a switch made in VS Code or a terminal
    doesn't leave Precursor pulling one branch into another.
    """
    branch = await git.current_branch(root)
    if branch is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The working copy is on a detached HEAD — check out a branch first.",
        )
    try:
        git.check_branch(branch)
    except git.GitInputError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if ws.branch != branch:
        ws.branch = branch
        await session.commit()
    return branch


async def _after_tree_change(root: Path, session: AsyncSession) -> None:
    """Files changed on disk under git's hands: re-read definitions they hold."""
    if _holds_definitions(root):
        await definition_anchors.refresh(session)


@router.get("/{workspace_id}/git/status", response_model=GitStatus)
async def git_status(workspace_id: int, session: AsyncSession = Depends(get_session)) -> GitStatus:
    ws = await _get_git_workspace(workspace_id, session)
    try:
        return await git.status(workspace_root(ws), ws.subdir)
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/{workspace_id}/git/fetch", response_model=GitActionResult)
async def git_fetch(
    workspace_id: int, session: AsyncSession = Depends(get_session)
) -> GitActionResult:
    """Update the remote-tracking branch, so ahead/behind are current. Changes no file."""
    ws = await _get_git_workspace(workspace_id, session)
    root = workspace_root(ws)
    try:
        before = await git.status(root, ws.subdir)
        if before.detached or before.upstream is None:
            # Nothing on the remote to compare with (yet): not a failure.
            return GitActionResult(
                ok=True,
                detail="Nothing to fetch for this branch.",
                local_path=str(root),
                status=before,
            )
        branch = await _checked_out_branch(ws, root, session)
        token = await resolve_github_token(session)
        ok, detail = await git.fetch(root, branch, token)
        st = await git.status(root, ws.subdir)
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return GitActionResult(ok=ok, detail=detail, local_path=str(root), status=st)


@router.post("/{workspace_id}/git/pull", response_model=GitActionResult)
async def git_pull(
    workspace_id: int, session: AsyncSession = Depends(get_session)
) -> GitActionResult:
    ws = await _get_git_workspace(workspace_id, session)
    root = workspace_root(ws)
    try:
        branch = await _checked_out_branch(ws, root, session)
        if (await git.status(root, ws.subdir)).upstream is None:
            return GitActionResult(
                ok=False,
                detail="Nothing to pull: this branch isn't on the remote yet. Push publishes it.",
                local_path=str(root),
                status=await git.status(root, ws.subdir),
            )
        token = await resolve_github_token(session)
        ok, detail = await git.pull(root, branch, token)
        st = await git.status(root, ws.subdir)
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if ok:
        ws.last_synced_at = datetime.now(UTC)
        await session.commit()
        # Files mode: a pull into the definitions folder applies at once (new
        # files, changed steps); anything that widens permissions waits for
        # review as usual.
        await _after_tree_change(root, session)
    return GitActionResult(
        ok=ok,
        detail=detail,
        needs_manual_merge=not ok,
        local_path=str(root),
        status=st,
    )


async def _commit(root: Path, payload: CommitRequest) -> tuple[bool, str]:
    if payload.paths is not None:
        return await git.commit_paths(root, payload.message, payload.paths)
    return await git.commit_all(root, payload.message)


async def _push(ws: Workspace, root: Path, branch: str, session: AsyncSession) -> GitActionResult:
    token = await resolve_github_token(session)
    pushed, detail = await git.push(root, branch, token)
    st = await git.status(root, ws.subdir)
    if pushed:
        ws.last_synced_at = datetime.now(UTC)
        await session.commit()
    return GitActionResult(
        ok=pushed,
        detail=detail,
        # Only a push the remote refused for being behind needs a pull/merge.
        needs_manual_merge=not pushed and git.push_rejected(detail),
        local_path=str(root),
        status=st,
    )


@router.post("/{workspace_id}/git/commit", response_model=GitActionResult)
async def git_commit(
    workspace_id: int,
    payload: CommitRequest,
    session: AsyncSession = Depends(get_session),
) -> GitActionResult:
    """Commit locally (the selected ``paths``, or everything); nothing is pushed."""
    ws = await _get_git_workspace(workspace_id, session)
    root = workspace_root(ws)
    try:
        committed, detail = await _commit(root, payload)
        st = await git.status(root, ws.subdir)
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return GitActionResult(ok=committed, detail=detail, local_path=str(root), status=st)


@router.post("/{workspace_id}/git/push", response_model=GitActionResult)
async def git_push(
    workspace_id: int, session: AsyncSession = Depends(get_session)
) -> GitActionResult:
    """Push the checked-out branch; publish it (set its upstream) the first time."""
    ws = await _get_git_workspace(workspace_id, session)
    root = workspace_root(ws)
    try:
        branch = await _checked_out_branch(ws, root, session)
        return await _push(ws, root, branch, session)
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/{workspace_id}/git/commit-push", response_model=GitActionResult)
async def git_commit_push(
    workspace_id: int,
    payload: CommitRequest,
    session: AsyncSession = Depends(get_session),
) -> GitActionResult:
    ws = await _get_git_workspace(workspace_id, session)
    root = workspace_root(ws)
    try:
        branch = await _checked_out_branch(ws, root, session)
        committed, commit_detail = await _commit(root, payload)
        if not committed:
            st = await git.status(root, ws.subdir)
            return GitActionResult(ok=False, detail=commit_detail, local_path=str(root), status=st)
        result = await _push(ws, root, branch, session)
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if not result.ok:
        result.detail = f"Committed locally but push failed: {result.detail}"
    return result


@router.post("/{workspace_id}/git/discard", response_model=GitStatus)
async def git_discard(
    workspace_id: int,
    path: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> GitStatus:
    """Put a file back as HEAD has it; a file HEAD doesn't have is deleted."""
    ws = await _get_git_workspace(workspace_id, session)
    root = workspace_root(ws)
    try:
        await git.discard(root, path)
        st = await git.status(root, ws.subdir)
    except (git.GitError, fs.UnsafePathError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await _after_tree_change(root, session)
    return st


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


@router.get("/{workspace_id}/git/file-versions", response_model=FileVersions)
async def git_file_versions(
    workspace_id: int,
    path: str = Query(...),
    original_path: str | None = Query(None),
    base: str | None = Query(None),
    head: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> FileVersions:
    """One file at ``base`` and in the working copy (or at ``head``), for the diff editor.

    Paths are relative to the repository root; ``original_path`` is a rename's
    old name. ``base`` defaults to HEAD for the working copy; with ``head`` and
    no ``base`` (a root commit) the original side is empty. Over 2 MB, or
    binary, only the flag comes back. Revisions are commit ids (or HEAD): a
    commit's parent comes from ``/git/commits/{sha}``.
    """
    ws = await _get_git_workspace(workspace_id, session)
    root = workspace_root(ws)
    if base is None and head is None:
        base = "HEAD"
    try:
        return await git.file_versions(root, path, original_rel=original_path, base=base, head=head)
    except (git.GitError, fs.UnsafePathError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/{workspace_id}/git/branches", response_model=GitBranches)
async def git_branches(
    workspace_id: int, session: AsyncSession = Depends(get_session)
) -> GitBranches:
    """Local branches and the remote's (asked of the remote: a single-branch
    clone doesn't know them). ``remote_error`` says why the latter is empty."""
    ws = await _get_git_workspace(workspace_id, session)
    token = await resolve_github_token(session)
    try:
        return await git.branches(workspace_root(ws), token)
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


def _branch_error(exc: git.GitError) -> HTTPException:
    if isinstance(exc, git.GitRefused):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, git.GitNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


async def _after_branch_change(ws: Workspace, root: Path, session: AsyncSession) -> GitStatus:
    await _checked_out_branch(ws, root, session)  # Workspace.branch follows
    await _after_tree_change(root, session)
    return await git.status(root, ws.subdir)


@router.post("/{workspace_id}/git/switch", response_model=GitActionResult)
async def git_switch(
    workspace_id: int,
    payload: BranchRequest,
    session: AsyncSession = Depends(get_session),
) -> GitActionResult:
    """Check out another branch, local or only on the remote.

    409 when anything uncommitted could be lost (the files are named), during a
    merge, or on a detached HEAD: nothing is ever forced, discarded or stashed.
    """
    ws = await _get_git_workspace(workspace_id, session)
    root = workspace_root(ws)
    token = await resolve_github_token(session)
    try:
        await git.switch(root, payload.name, token)
        st = await _after_branch_change(ws, root, session)
    except git.GitError as exc:
        raise _branch_error(exc) from exc
    return GitActionResult(
        ok=True, detail=f"Switched to {st.branch}.", local_path=str(root), status=st
    )


@router.post(
    "/{workspace_id}/git/branches",
    response_model=GitActionResult,
    status_code=status.HTTP_201_CREATED,
)
async def git_create_branch(
    workspace_id: int,
    payload: BranchRequest,
    session: AsyncSession = Depends(get_session),
) -> GitActionResult:
    """Create a branch from HEAD and check it out. It isn't published (no
    upstream) until pushed. 409 if the name is taken here or on the remote."""
    ws = await _get_git_workspace(workspace_id, session)
    root = workspace_root(ws)
    token = await resolve_github_token(session)
    try:
        await git.create_branch(root, payload.name, token)
        st = await _after_branch_change(ws, root, session)
    except git.GitError as exc:
        raise _branch_error(exc) from exc
    return GitActionResult(ok=True, detail=f"Created {st.branch}.", local_path=str(root), status=st)


@router.post("/{workspace_id}/git/merge", response_model=GitActionResult)
async def git_merge(
    workspace_id: int, session: AsyncSession = Depends(get_session)
) -> GitActionResult:
    """Merge the remote's branch into the checked-out one (a merge commit).

    ``ok=false`` with ``status.merging`` means it stopped on conflicts, to be
    resolved (``/git/conflict``, ``/git/resolve``) then completed or aborted.
    409 on uncommitted changes, a detached HEAD or a merge already running.
    """
    ws = await _get_git_workspace(workspace_id, session)
    root = workspace_root(ws)
    token = await resolve_github_token(session)
    try:
        branch = await _checked_out_branch(ws, root, session)
        clean, detail = await git.merge(root, branch, token)
        st = await git.status(root, ws.subdir)
    except git.GitError as exc:
        raise _branch_error(exc) from exc
    await _after_tree_change(root, session)
    return GitActionResult(ok=clean, detail=detail, local_path=str(root), status=st)


@router.get("/{workspace_id}/git/conflict", response_model=GitConflict)
async def git_conflict(
    workspace_id: int,
    path: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> GitConflict:
    """A conflicted file's base / ours / theirs (index stages 1, 2, 3)."""
    ws = await _get_git_workspace(workspace_id, session)
    try:
        return await git.conflict(workspace_root(ws), path)
    except git.GitError as exc:
        raise _branch_error(exc) from exc


@router.post("/{workspace_id}/git/resolve", response_model=GitStatus)
async def git_resolve(
    workspace_id: int,
    payload: ResolveRequest,
    session: AsyncSession = Depends(get_session),
) -> GitStatus:
    """Mark one conflict resolved: as edited (refused while markers remain) or
    by keeping one side's version (or deletion)."""
    ws = await _get_git_workspace(workspace_id, session)
    root = workspace_root(ws)
    try:
        await git.resolve(root, payload.path, payload.side)
        st = await git.status(root, ws.subdir)
    except fs.UnsafePathError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except git.GitError as exc:
        raise _branch_error(exc) from exc
    if payload.side is not None:
        await _after_tree_change(root, session)
    return st


@router.post("/{workspace_id}/git/merge/complete", response_model=GitActionResult)
async def git_merge_complete(
    workspace_id: int, session: AsyncSession = Depends(get_session)
) -> GitActionResult:
    """Commit the merge; 409 while any file is still conflicted."""
    ws = await _get_git_workspace(workspace_id, session)
    root = workspace_root(ws)
    try:
        detail = await git.complete_merge(root)
        st = await git.status(root, ws.subdir)
    except git.GitError as exc:
        raise _branch_error(exc) from exc
    await _after_tree_change(root, session)
    return GitActionResult(ok=True, detail=detail, local_path=str(root), status=st)


@router.post("/{workspace_id}/git/merge/abort", response_model=GitActionResult)
async def git_merge_abort(
    workspace_id: int, session: AsyncSession = Depends(get_session)
) -> GitActionResult:
    """Undo the merge: branch and files as they were before it."""
    ws = await _get_git_workspace(workspace_id, session)
    root = workspace_root(ws)
    try:
        await git.abort_merge(root)
        st = await git.status(root, ws.subdir)
    except git.GitError as exc:
        raise _branch_error(exc) from exc
    await _after_tree_change(root, session)
    return GitActionResult(ok=True, detail="Merge aborted.", local_path=str(root), status=st)


@router.get("/{workspace_id}/git/log", response_model=GitLog)
async def git_log(
    workspace_id: int,
    limit: int = Query(50, ge=1, le=git.MAX_LOG_PAGE),
    skip: int = Query(0, ge=0),
    path: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> GitLog:
    """A page of the checked-out branch's history; with ``path``, that file's."""
    ws = await _get_git_workspace(workspace_id, session)
    try:
        return await git.log(workspace_root(ws), limit=limit, skip=skip, rel=path)
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/{workspace_id}/git/commits/{sha}", response_model=GitCommitDetail)
async def git_commit_detail(
    workspace_id: int, sha: str, session: AsyncSession = Depends(get_session)
) -> GitCommitDetail:
    """One commit: message, first parent (null for a root commit) and changed files."""
    ws = await _get_git_workspace(workspace_id, session)
    try:
        return await git.commit_detail(workspace_root(ws), sha)
    except git.GitNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


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
