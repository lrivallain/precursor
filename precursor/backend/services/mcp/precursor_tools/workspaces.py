"""Workspace browsing and (strict, opt-in) workspace write tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote

from sqlalchemy import select

from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    Workspace,
)
from precursor.backend.services import workspace_fs
from precursor.backend.services.links import app_base_url
from precursor.backend.services.mcp.workspace_links import with_open_link
from precursor.backend.services.workspaces import WorkspaceError, browse_root
from precursor.backend.services.workspaces import create_workspace as create_workspace_row

if TYPE_CHECKING:
    pass
from precursor.backend.services.mcp.precursor_tools._core import _GATED, _section_enabled, mcp

# --------------------------------------------------------------------------
# workspaces (sandboxed to a workspace's on-disk working tree)
# --------------------------------------------------------------------------
# Cap how much file text a read hands back to the model.
_DEFAULT_MAX_BYTES = 100_000


# Cap how many entries list_workspace_files returns so a huge repo can't blow
# the context.
_MAX_LIST_ENTRIES = 2000


async def _workspace_gate(section: str) -> dict[str, Any] | None:
    if not await _section_enabled(section, strict=True):
        return {"error": _GATED.format(section=section)}
    return None


async def _load_workspace(workspace_id: int) -> Workspace | None:
    async with SessionLocal() as session:
        return await session.get(Workspace, workspace_id)


@mcp.tool()
async def list_workspaces() -> dict[str, Any]:
    """List available workspaces (id, slug, name) you can operate on.

    Call this first to find the ``workspace_id`` for the other workspace tools.
    """
    if (gated := await _workspace_gate("workspaces")) is not None:
        return gated
    async with SessionLocal() as session:
        rows = (await session.execute(select(Workspace))).scalars().all()
    return {
        "workspaces": [
            {
                "id": w.id,
                "slug": w.slug,
                "name": w.name,
                "kind": w.kind,
                "ready": w.cloned_at is not None,
            }
            for w in rows
        ]
    }


@mcp.tool()
async def list_workspace_files(workspace_id: int, subdir: str | None = None) -> dict[str, Any]:
    """List files and folders in a workspace.

    Paths are POSIX-style and relative to the workspace root. ``.git`` is
    hidden. Optionally restrict to ``subdir``.
    """
    if (gated := await _workspace_gate("workspaces")) is not None:
        return gated
    ws = await _load_workspace(workspace_id)
    if ws is None:
        return {"error": f"Workspace {workspace_id} not found"}
    root = browse_root(ws)
    if not root.exists():
        return {"error": "Workspace is not ready yet"}
    try:
        base = workspace_fs.safe_join(root, subdir) if subdir else root
    except workspace_fs.UnsafePathError as exc:
        return {"error": str(exc)}
    nodes = workspace_fs.list_tree(base)
    return {
        "root": str(subdir or ""),
        "entries": [
            {"path": n.path, "name": n.name, "type": n.type} for n in nodes[:_MAX_LIST_ENTRIES]
        ],
        "truncated": len(nodes) > _MAX_LIST_ENTRIES,
        "count": len(nodes),
    }


@mcp.tool()
async def read_workspace_file(
    workspace_id: int, path: str, max_bytes: int = _DEFAULT_MAX_BYTES
) -> dict[str, Any]:
    """Read a UTF-8 text file from a workspace.

    Returns ``content`` (truncated past ``max_bytes``). Binary files and paths
    that escape the workspace are rejected.
    """
    if (gated := await _workspace_gate("workspaces")) is not None:
        return gated
    ws = await _load_workspace(workspace_id)
    if ws is None:
        return {"error": f"Workspace {workspace_id} not found"}
    if max_bytes <= 0 or max_bytes > 1_000_000:
        max_bytes = _DEFAULT_MAX_BYTES
    try:
        content = workspace_fs.read_text(browse_root(ws), path)
    except workspace_fs.UnsafePathError as exc:
        return {"error": str(exc)}
    except FileNotFoundError:
        return {"error": f"File not found: {path}"}
    except IsADirectoryError:
        return {"error": f"Not a file: {path}"}
    except UnicodeDecodeError:
        return {"error": f"File is not UTF-8 text: {path}"}
    truncated = len(content.encode("utf-8")) > max_bytes
    if truncated:
        content = content.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
    return with_open_link({"path": path, "content": content, "truncated": truncated}, ws.slug, path)


@mcp.tool()
async def create_workspace(
    name: str,
    kind: Literal["local", "git"] = "local",
    repo_url: str | None = None,
    branch: str = "main",
    subdir: str | None = None,
    slug: str | None = None,
) -> dict[str, Any]:
    """Create a workspace: an empty local folder, or a clone of a git repository.

    ``kind="local"`` makes an empty folder Precursor owns. ``kind="git"``
    clones ``repo_url`` at ``branch`` (``repo_url`` is required) using the
    connected GitHub account for private repositories. ``subdir`` scopes the
    file browser to a folder inside the repo. ``slug`` defaults to a slug of
    ``name`` and is made unique. Returns the new ``workspace_id`` to pass to the
    other workspace tools, plus an in-app ``url``.
    """
    if (gated := await _workspace_gate("workspaces_write")) is not None:
        return gated
    if not name.strip():
        return {"error": "A workspace name is required."}
    async with SessionLocal() as session:
        try:
            ws = await create_workspace_row(
                session,
                name=name.strip(),
                kind=kind,
                repo_url=repo_url,
                branch=branch,
                subdir=subdir,
                slug=slug,
            )
        except WorkspaceError as exc:
            return {"error": str(exc)}
        return {
            "workspace_id": ws.id,
            "slug": ws.slug,
            "name": ws.name,
            "kind": ws.kind,
            "ready": ws.cloned_at is not None,
            "url": f"{app_base_url()}/ws/{quote(ws.slug, safe='')}",
        }


@mcp.tool()
async def create_workspace_file(workspace_id: int, path: str, content: str = "") -> dict[str, Any]:
    """Create a new file in a workspace. Fails if the path already exists.

    Parent folders are created as needed. Use ``write_workspace_file`` to
    overwrite an existing file.
    """
    if (gated := await _workspace_gate("workspaces_write")) is not None:
        return gated
    ws = await _load_workspace(workspace_id)
    if ws is None:
        return {"error": f"Workspace {workspace_id} not found"}
    root = browse_root(ws)
    if not root.exists():
        return {"error": "Workspace is not ready yet"}
    try:
        workspace_fs.refuse_definitions_for_tools(root, path)
        workspace_fs.create_file(root, path, content)
    except workspace_fs.UnsafePathError as exc:
        return {"error": str(exc)}
    except FileExistsError:
        return {"error": f"File already exists: {path}"}
    return with_open_link({"path": path, "created": True}, ws.slug, path)


@mcp.tool()
async def write_workspace_file(workspace_id: int, path: str, content: str) -> dict[str, Any]:
    """Create or overwrite a file in a workspace with ``content``.

    Parent folders are created as needed. Use ``create_workspace_file`` to
    fail when the file already exists.
    """
    if (gated := await _workspace_gate("workspaces_write")) is not None:
        return gated
    ws = await _load_workspace(workspace_id)
    if ws is None:
        return {"error": f"Workspace {workspace_id} not found"}
    root = browse_root(ws)
    if not root.exists():
        return {"error": "Workspace is not ready yet"}
    try:
        workspace_fs.refuse_definitions_for_tools(root, path)
        workspace_fs.write_text(root, path, content)
    except workspace_fs.UnsafePathError as exc:
        return {"error": str(exc)}
    return with_open_link(
        {"path": path, "written": True, "bytes": len(content.encode("utf-8"))},
        ws.slug,
        path,
    )


@mcp.tool()
async def create_workspace_folder(workspace_id: int, path: str) -> dict[str, Any]:
    """Create a new folder in a workspace. Fails if the path already exists."""
    if (gated := await _workspace_gate("workspaces_write")) is not None:
        return gated
    ws = await _load_workspace(workspace_id)
    if ws is None:
        return {"error": f"Workspace {workspace_id} not found"}
    root = browse_root(ws)
    if not root.exists():
        return {"error": "Workspace is not ready yet"}
    try:
        workspace_fs.refuse_definitions_for_tools(root, path)
        workspace_fs.create_dir(root, path)
    except workspace_fs.UnsafePathError as exc:
        return {"error": str(exc)}
    except FileExistsError:
        return {"error": f"Path already exists: {path}"}
    return {"path": path, "created": True}
