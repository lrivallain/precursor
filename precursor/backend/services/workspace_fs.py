"""Safe filesystem operations within a Workspace's working tree.

Every path coming from the API is treated as untrusted: it is resolved
against the workspace root and rejected if it escapes that root (path traversal).
The ``.git`` directory is always hidden from listings and off-limits to
reads/writes.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from precursor.backend.schemas import FileNode


class UnsafePathError(ValueError):
    """Raised when a requested path escapes the workspace root."""


# Files we never surface or let the user edit through the API.
_HIDDEN_TOP = {".git"}
# Extensions considered text-editable in the UI. Everything else is treated
# as opaque (listed but not opened for editing).
TEXT_SUFFIXES = {
    ".md",
    ".markdown",
    ".txt",
    ".rst",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".csv",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".py",
    ".sh",
    ".drawio",
    ".env",
    ".gitignore",
}


def safe_join(root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``root``; raise ``UnsafePathError`` on escape."""
    rel = (rel or "").strip().lstrip("/")
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise UnsafePathError(f"Path '{rel}' escapes the workspace root")
    # Block anything inside .git.
    parts = target.relative_to(root_resolved).parts if target != root_resolved else ()
    if parts and parts[0] in _HIDDEN_TOP:
        raise UnsafePathError(f"Path '{rel}' is not accessible")
    return target


def refuse_definitions_for_tools(root: Path, rel: str) -> None:
    """Raise ``UnsafePathError`` when a *tool* would write the definitions folder.

    Agent and workflow definition files declare what an agent may do, so an
    assistant writing them could grant itself more. The MCP file tools call this
    before any write; people editing in the Files section don't, and a change
    that widens permissions is still held for review before it can run.
    """
    from precursor.backend.config import get_settings

    target = safe_join(root, rel)
    protected = Path(get_settings().definitions_dir).resolve()
    if target == protected or protected in target.parents:
        raise UnsafePathError(
            "Agent and workflow definition files are read-only for tools; edit them in the app"
        )


def is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_SUFFIXES


def list_tree(root: Path) -> list[FileNode]:
    """Return a flat, sorted list of all files/dirs under ``root``.

    Directories come before files at each level; ``.git`` is skipped.
    Paths are POSIX-style and relative to ``root``.
    """
    root = root.resolve()
    nodes: list[FileNode] = []
    if not root.is_dir():
        return nodes

    for current, dirnames, filenames in root.walk():
        # Prune hidden/system dirs in place so os.walk doesn't descend them.
        dirnames[:] = sorted(d for d in dirnames if d not in _HIDDEN_TOP)
        filenames = sorted(filenames)
        for d in dirnames:
            full = current / d
            nodes.append(
                FileNode(
                    path=full.relative_to(root).as_posix(),
                    name=d,
                    type="dir",
                )
            )
        for f in filenames:
            full = current / f
            nodes.append(
                FileNode(
                    path=full.relative_to(root).as_posix(),
                    name=f,
                    type="file",
                )
            )
    return nodes


def read_text(root: Path, rel: str) -> str:
    target = safe_join(root, rel)
    if not target.is_file():
        raise FileNotFoundError(rel)
    return target.read_text(encoding="utf-8")


def write_text(root: Path, rel: str, content: str) -> None:
    target = safe_join(root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def create_file(root: Path, rel: str, content: str = "") -> None:
    target = safe_join(root, rel)
    if target.exists():
        raise FileExistsError(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def create_dir(root: Path, rel: str) -> None:
    target = safe_join(root, rel)
    if target.exists():
        raise FileExistsError(rel)
    target.mkdir(parents=True)


def delete_file(root: Path, rel: str) -> None:
    target = safe_join(root, rel)
    if not target.exists():
        raise FileNotFoundError(rel)
    if target.is_dir():
        raise IsADirectoryError(rel)
    target.unlink()


def delete_dir(root: Path, rel: str) -> int:
    """Delete the folder ``rel`` and everything in it; returns how many files went.

    Never the root itself. A symlink to a folder loses only the link: its
    target, which may hold anything, is left alone.
    """
    target = safe_join(root, rel)
    parts = [p for p in Path((rel or "").strip().strip("/")).parts if p not in ("", ".")]
    if target == root.resolve() or not parts:
        raise UnsafePathError("The workspace's own folder can't be deleted")
    lexical = root.joinpath(*parts)
    if lexical.is_symlink():
        lexical.unlink()
        return 0
    if not target.exists():
        raise FileNotFoundError(rel)
    if not target.is_dir():
        raise NotADirectoryError(rel)
    files = sum(1 for p in target.rglob("*") if p.is_file() or p.is_symlink())
    # rmtree doesn't follow symlinks inside the folder: it removes the links.
    shutil.rmtree(target)
    return files


def rename(root: Path, src_rel: str, dst_rel: str) -> None:
    """Rename/move a file or directory from ``src_rel`` to ``dst_rel``.

    Both paths are validated against the root (no traversal, no ``.git``).
    Missing intermediate folders in the destination are created. Raises
    ``FileNotFoundError`` if the source is missing and ``FileExistsError`` if
    something already lives at the destination.
    """
    src = safe_join(root, src_rel)
    dst = safe_join(root, dst_rel)
    if not src.exists():
        raise FileNotFoundError(src_rel)
    if src.resolve() == dst.resolve():
        return
    if src.resolve() in dst.resolve().parents:
        raise UnsafePathError(f"Cannot move '{src_rel}' into its own subdirectory")
    if dst.exists():
        raise FileExistsError(dst_rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
