"""Async Git operations for Workspaces.

Shells out to the system ``git`` binary via ``asyncio`` subprocesses — the
same posture used elsewhere for the ``gh`` CLI. ``git`` must be on PATH.

Authentication: a GitHub token (when available) is injected per-invocation
through an ``http.extraheader`` config flag so it is never written to disk in
``.git/config``. The remote URL stored on disk stays token-free.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import shutil
from pathlib import Path

from precursor.backend.schemas.workspace import GitFileStatus, GitStatus

logger = logging.getLogger(__name__)

_TIMEOUT = 120.0


class GitError(RuntimeError):
    def __init__(self, message: str, *, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


def git_available() -> bool:
    return shutil.which("git") is not None


def _auth_args(token: str | None) -> list[str]:
    """Per-invocation auth header so the token never lands in .git/config."""
    if not token:
        return []
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.extraheader=AUTHORIZATION: basic {basic}"]


async def _run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    token: str | None = None,
) -> tuple[int, str, str]:
    if not git_available():
        raise GitError("git is not installed or not on PATH")
    cmd = ["git", *_auth_args(token), *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
    except TimeoutError as exc:
        proc.kill()
        raise GitError("git command timed out") from exc
    return (
        proc.returncode or 0,
        stdout_b.decode(errors="replace"),
        stderr_b.decode(errors="replace"),
    )


async def clone(repo_url: str, dest: Path, branch: str, token: str | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise GitError(f"Destination already exists: {dest}")
    code, _out, err = await _run_git(
        ["clone", "--branch", branch, "--single-branch", repo_url, str(dest)],
        token=token,
    )
    if code != 0:
        # The clone may have left a partial directory behind.
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        raise GitError(f"Clone failed: {err.strip()}", stderr=err)


async def pull(path: Path, branch: str, token: str | None) -> tuple[bool, str]:
    """Fast-forward-only pull.

    Returns ``(ok, detail)``. ``ok=False`` means the branch has diverged or a
    conflict exists and the user must resolve it with the git CLI.
    """
    await _run_git(["fetch", "origin", branch], cwd=path, token=token)
    code, out, err = await _run_git(
        ["merge", "--ff-only", f"origin/{branch}"], cwd=path, token=token
    )
    if code == 0:
        return True, (out or err).strip() or "Up to date."
    return False, (err or out).strip()


async def commit_all(path: Path, message: str) -> tuple[bool, str]:
    """Stage every change and commit. Returns ``(committed, detail)``."""
    await _run_git(["add", "-A"], cwd=path)
    code, out, err = await _run_git(["commit", "-m", message], cwd=path)
    if code == 0:
        return True, out.strip()
    detail = (out + err).strip()
    if "nothing to commit" in detail:
        return False, "Nothing to commit."
    raise GitError(f"Commit failed: {detail}", stderr=err)


async def commit_paths(path: Path, message: str, paths: list[str]) -> tuple[bool, str]:
    """Stage and commit only ``paths`` (adds, edits, deletions).

    Returns ``(committed, detail)``. Leaves other changes untouched in the
    working tree so the user can commit them separately later.
    """
    if not paths:
        return False, "No files selected."
    # ``add -A -- <paths>`` stages additions, modifications and deletions for
    # exactly those pathspecs; ``commit -- <paths>`` records only them.
    await _run_git(["add", "-A", "--", *paths], cwd=path)
    code, out, err = await _run_git(["commit", "-m", message, "--", *paths], cwd=path)
    if code == 0:
        return True, out.strip()
    detail = (out + err).strip()
    if "nothing to commit" in detail or "no changes added" in detail:
        return False, "Nothing to commit."
    raise GitError(f"Commit failed: {detail}", stderr=err)


async def diff_file(path: Path, rel: str) -> tuple[str, bool]:
    """Return ``(diff_text, is_binary)`` for a single working-tree path.

    Covers staged + unstaged edits and deletions against HEAD. Untracked
    files are rendered as an all-added diff so they preview consistently.
    """
    _c, st_out, _e = await _run_git(["status", "--porcelain", "--", rel], cwd=path)
    code_part = st_out[:2] if st_out else ""
    if code_part.strip() == "??":
        # Untracked: diff against the empty blob via the no-index mode.
        # ``--no-index`` returns exit 1 when the files differ (expected here).
        _code, out, _err = await _run_git(["diff", "--no-index", "--", "/dev/null", rel], cwd=path)
        return out, "Binary files" in out
    _code, out, _err = await _run_git(["diff", "HEAD", "--", rel], cwd=path)
    return out, "Binary files" in out


async def push(path: Path, branch: str, token: str | None) -> tuple[bool, str]:
    code, out, err = await _run_git(["push", "origin", branch], cwd=path, token=token)
    if code == 0:
        return True, (out or err).strip() or "Pushed."
    return False, (err or out).strip()


async def discard(path: Path, rel: str) -> None:
    await _run_git(["checkout", "--", rel], cwd=path)


async def status(path: Path) -> GitStatus:
    # Current branch name.
    _c, branch_out, _e = await _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    branch = branch_out.strip() or "HEAD"

    ahead: int | None = None
    behind: int | None = None
    code, counts, _e2 = await _run_git(
        ["rev-list", "--left-right", "--count", f"origin/{branch}...HEAD"],
        cwd=path,
    )
    if code == 0 and counts.strip():
        parts = counts.split()
        if len(parts) == 2:
            behind, ahead = int(parts[0]), int(parts[1])

    _c3, porcelain, _e3 = await _run_git(["status", "--porcelain"], cwd=path)
    files: list[GitFileStatus] = []
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        code_part = line[:2]
        name = line[3:]
        files.append(GitFileStatus(path=name, code=code_part))

    return GitStatus(
        branch=branch,
        ahead=ahead,
        behind=behind,
        dirty=bool(files),
        files=files,
    )
