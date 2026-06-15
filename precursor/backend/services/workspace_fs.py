"""Safe filesystem operations within a Workspace's working tree.

Every path coming from the API is treated as untrusted: it is resolved
against the workspace root and rejected if it escapes that root (path traversal).
The ``.git`` directory is always hidden from listings and off-limits to
reads/writes.
"""

from __future__ import annotations

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
