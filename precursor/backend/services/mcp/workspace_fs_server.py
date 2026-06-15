"""Built-in MCP server: Workspace filesystem (sandboxed).

Runs as a stdio subprocess (like ``fetch_server``). Exposes read/write tools
scoped *strictly* to a Workspace's on-disk working tree — every path is routed
through :func:`workspace_fs.safe_join`, which rejects traversal outside the
workspace root and blocks ``.git``. Nothing outside ``workspaces_dir/<slug>``
is ever reachable.

Tools:
- ``list_workspaces()`` — discover workspace ids/slugs to operate on.
- ``list_files(workspace_id, subdir=None)`` — flat file tree.
- ``read_file(workspace_id, path, max_bytes=...)`` — UTF-8 file contents.
- ``create_file(workspace_id, path, content="")`` — fails if it exists.
- ``write_file(workspace_id, path, content)`` — create or overwrite.
- ``create_folder(workspace_id, path)`` — fails if it exists.

Writes land in the working tree only; committing/pushing to git stays a
separate, explicit user action (the Workspace UI ``/commit`` endpoint).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import Workspace
from precursor.backend.services import workspace_fs as fs

# Cap how much file text we hand back to the model in one read.
_DEFAULT_MAX_BYTES = 100_000
# Cap how many entries list_files returns so a huge repo can't blow the context.
_MAX_LIST_ENTRIES = 2000

mcp = FastMCP("workspace-fs")


def _browse_root(ws: Workspace) -> Path:
    """File root for a workspace: ``workspaces_dir/<slug>[/<subdir>]``.

    Mirrors ``routers.workspaces.browse_root`` without importing the router
    (which pulls in the LLM/git stack we don't need here).
    """
    root = Path(get_settings().workspaces_dir) / ws.slug
    if ws.subdir:
        root = root / ws.subdir.strip("/")
    return root


async def _load_workspace(workspace_id: int) -> Workspace | None:
    async with SessionLocal() as session:
        return await session.get(Workspace, workspace_id)


@mcp.tool()
async def list_workspaces() -> dict[str, Any]:
    """List available workspaces (id, slug, name) you can operate on.

    Call this first to find the ``workspace_id`` for the other tools.
    """
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
async def list_files(workspace_id: int, subdir: str | None = None) -> dict[str, Any]:
    """List files and folders in a workspace.

    Paths are POSIX-style and relative to the workspace root. ``.git`` is
    hidden. Optionally restrict to ``subdir``.
    """
    ws = await _load_workspace(workspace_id)
    if ws is None:
        return {"error": f"Workspace {workspace_id} not found"}
    root = _browse_root(ws)
    if not root.exists():
        return {"error": "Workspace is not ready yet"}
    try:
        base = fs.safe_join(root, subdir) if subdir else root
    except fs.UnsafePathError as exc:
        return {"error": str(exc)}
    nodes = fs.list_tree(base)
    truncated = len(nodes) > _MAX_LIST_ENTRIES
    return {
        "root": str(subdir or ""),
        "entries": [
            {"path": n.path, "name": n.name, "type": n.type} for n in nodes[:_MAX_LIST_ENTRIES]
        ],
        "truncated": truncated,
        "count": len(nodes),
    }


@mcp.tool()
async def read_file(
    workspace_id: int, path: str, max_bytes: int = _DEFAULT_MAX_BYTES
) -> dict[str, Any]:
    """Read a UTF-8 text file from a workspace.

    Returns ``content`` (truncated past ``max_bytes``). Binary files and paths
    that escape the workspace are rejected.
    """
    ws = await _load_workspace(workspace_id)
    if ws is None:
        return {"error": f"Workspace {workspace_id} not found"}
    if max_bytes <= 0 or max_bytes > 1_000_000:
        max_bytes = _DEFAULT_MAX_BYTES
    try:
        content = fs.read_text(_browse_root(ws), path)
    except fs.UnsafePathError as exc:
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
    return {"path": path, "content": content, "truncated": truncated}


@mcp.tool()
async def create_file(workspace_id: int, path: str, content: str = "") -> dict[str, Any]:
    """Create a new file in a workspace. Fails if the path already exists.

    Parent folders are created as needed. Use ``write_file`` to overwrite an
    existing file.
    """
    ws = await _load_workspace(workspace_id)
    if ws is None:
        return {"error": f"Workspace {workspace_id} not found"}
    if not _browse_root(ws).exists():
        return {"error": "Workspace is not ready yet"}
    try:
        fs.create_file(_browse_root(ws), path, content)
    except fs.UnsafePathError as exc:
        return {"error": str(exc)}
    except FileExistsError:
        return {"error": f"File already exists: {path}"}
    return {"path": path, "created": True}


@mcp.tool()
async def write_file(workspace_id: int, path: str, content: str) -> dict[str, Any]:
    """Create or overwrite a file in a workspace with ``content``.

    Parent folders are created as needed. Use ``create_file`` if you want to
    fail when the file already exists.
    """
    ws = await _load_workspace(workspace_id)
    if ws is None:
        return {"error": f"Workspace {workspace_id} not found"}
    if not _browse_root(ws).exists():
        return {"error": "Workspace is not ready yet"}
    try:
        fs.write_text(_browse_root(ws), path, content)
    except fs.UnsafePathError as exc:
        return {"error": str(exc)}
    return {"path": path, "written": True, "bytes": len(content.encode("utf-8"))}


@mcp.tool()
async def create_folder(workspace_id: int, path: str) -> dict[str, Any]:
    """Create a new folder in a workspace. Fails if the path already exists."""
    ws = await _load_workspace(workspace_id)
    if ws is None:
        return {"error": f"Workspace {workspace_id} not found"}
    if not _browse_root(ws).exists():
        return {"error": "Workspace is not ready yet"}
    try:
        fs.create_dir(_browse_root(ws), path)
    except fs.UnsafePathError as exc:
        return {"error": str(exc)}
    except FileExistsError:
        return {"error": f"Path already exists: {path}"}
    return {"path": path, "created": True}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
