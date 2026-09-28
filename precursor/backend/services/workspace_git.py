"""Async Git operations for Workspaces.

Shells out to the system ``git`` binary via ``asyncio`` subprocesses — the
same posture used elsewhere for the ``gh`` CLI. ``git`` must be on PATH.

Authentication: a GitHub token (when available) is injected per-invocation
through an ``http.extraheader`` config flag so it is never written to disk in
``.git/config``. The remote URL stored on disk stays token-free.

Every path, branch name and revision that comes from a request is checked here
before it reaches git (``check_path``, ``check_branch``, ``check_rev``), and git
runs with literal pathspecs, so ``:/`` or ``*`` can't widen what an operation
touches, and with terminal prompts off, so a missing credential fails instead
of hanging the request.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import shutil
from pathlib import Path, PurePosixPath

from precursor.backend.schemas.workspace import FileVersions, GitFileStatus, GitStatus
from precursor.backend.services import workspace_fs as fs

logger = logging.getLogger(__name__)

_TIMEOUT = 120.0

# Pushes git refuses because the remote moved on: a pull (and maybe a merge)
# is needed first, unlike an auth or network failure.
_PUSH_REJECTED = ("[rejected]", "non-fast-forward", "fetch first")


class GitError(RuntimeError):
    def __init__(self, message: str, *, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


class GitInputError(GitError):
    """A path, branch name or revision from a request that git must not see."""


def git_available() -> bool:
    return shutil.which("git") is not None


# --- Input checks ---------------------------------------------------------------


def check_path(rel: str) -> str:
    """A working-tree path relative to the repository root, normalised.

    Checked on its text rather than resolved: git addresses the path itself (a
    symlink, a deleted file), and it refuses to follow a symlinked directory.
    """
    text = (rel or "").strip()
    if not text or "\0" in text or "\\" in text:
        raise GitInputError(f"Invalid path: {rel!r}")
    pure = PurePosixPath(text)
    if pure.is_absolute() or re.match(r"^[A-Za-z]:", text):
        raise GitInputError(f"Path '{rel}' must be relative to the repository")
    parts = [p for p in pure.parts if p != "."]
    if not parts or ".." in parts:
        raise GitInputError(f"Path '{rel}' escapes the repository")
    if any(p.lower() == ".git" for p in parts):
        raise GitInputError(f"Path '{rel}' is not accessible")
    return "/".join(parts)


_REF_FORBIDDEN = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


def check_branch(name: str) -> str:
    """A branch name git would accept (``git check-ref-format --branch``).

    Also refuses a leading ``-`` (an option to git) and ``HEAD``.
    """
    branch = (name or "").strip()
    if (
        not branch
        or branch.startswith("-")
        or branch == "HEAD"
        or branch == "@"
        or _REF_FORBIDDEN.search(branch)
        or ".." in branch
        or "@{" in branch
        or "//" in branch
        or branch.startswith("/")
        or branch.endswith(("/", "."))
        or any(part.startswith(".") or part.endswith(".lock") for part in branch.split("/"))
    ):
        raise GitInputError(f"Invalid branch name: {name!r}")
    return branch


_SHA = re.compile(r"[0-9a-f]{4,64}")


def check_rev(rev: str) -> str:
    """``HEAD`` or an abbreviated/full commit id; nothing git could read as more."""
    value = (rev or "").strip()
    if value == "HEAD" or _SHA.fullmatch(value):
        return value
    raise GitInputError(f"Invalid revision: {rev!r}")


# --- Running git ----------------------------------------------------------------


def _auth_args(token: str | None) -> list[str]:
    """Per-invocation auth header so the token never lands in .git/config."""
    if not token:
        return []
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.extraheader=AUTHORIZATION: basic {basic}"]


def git_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        # Fail rather than wait on a prompt no one will see.
        GIT_TERMINAL_PROMPT="0",
        GCM_INTERACTIVE="never",
        # A pathspec is a path: no `:/`, globs or other magic.
        GIT_LITERAL_PATHSPECS="1",
        # Don't take the index lock just to refresh it (status runs often, and
        # the user may be running git in the same folder).
        GIT_OPTIONAL_LOCKS="0",
        # Messages are matched below ("nothing to commit"); paths are bytes.
        LC_ALL="C",
    )
    return env


async def _run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    token: str | None = None,
) -> tuple[int, str, str]:
    code, out, err = await _run_git_bytes(args, cwd=cwd, token=token)
    return code, out.decode(errors="replace"), err


async def _run_git_bytes(
    args: list[str],
    *,
    cwd: Path | None = None,
    token: str | None = None,
) -> tuple[int, bytes, str]:
    """``_run_git`` with stdout left as bytes (file contents)."""
    if not git_available():
        raise GitError("git is not installed or not on PATH")
    cmd = ["git", *_auth_args(token), *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd) if cwd else None,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=git_env(),
        )
    except OSError as exc:
        # Typically the working copy is gone (cwd missing).
        raise GitError(f"Cannot run git here: {exc}") from exc
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
    except TimeoutError as exc:
        proc.kill()
        raise GitError("git command timed out") from exc
    return proc.returncode or 0, stdout_b, stderr_b.decode(errors="replace")


async def _git(args: list[str], *, cwd: Path, what: str, token: str | None = None) -> str:
    """Run git and raise ``GitError`` (with ``what`` as context) when it fails."""
    code, out, err = await _run_git(args, cwd=cwd, token=token)
    if code != 0:
        raise GitError(f"{what}: {(err or out).strip()}", stderr=err)
    return out


# --- Operations -----------------------------------------------------------------


async def clone(repo_url: str, dest: Path, branch: str, token: str | None) -> None:
    branch = check_branch(branch)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise GitError(f"Destination already exists: {dest}")
    code, _out, err = await _run_git(
        # `--` so a URL can't be read as an option (`--upload-pack=…`).
        ["clone", "--branch", branch, "--single-branch", "--", repo_url, str(dest)],
        token=token,
    )
    if code != 0:
        # The clone may have left a partial directory behind.
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        raise GitError(f"Clone failed: {err.strip()}", stderr=err)


async def current_branch(path: Path) -> str | None:
    """The checked-out branch; None on a detached HEAD."""
    code, out, _err = await _run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=path)
    return (out.strip() or None) if code == 0 else None


def _tracking_ref(branch: str) -> str:
    return f"refs/remotes/origin/{branch}"


async def _track(path: Path, branch: str) -> None:
    """Make ``origin/<branch>`` a remote-tracking branch of this clone.

    A single-branch clone only maps the branch it was cloned with. For any
    other, git would neither keep ``origin/<branch>`` up to date nor accept it
    as an upstream, so ahead/behind and "publish" would silently not work.
    """
    _code, out, _err = await _run_git(["config", "--get-all", "remote.origin.fetch"], cwd=path)
    specs = set(out.split())
    if f"+refs/heads/*:{_tracking_ref('*')}" in specs:
        return
    if f"+refs/heads/{branch}:{_tracking_ref(branch)}" not in specs:
        await _git(
            ["remote", "set-branches", "--add", "origin", branch],
            cwd=path,
            what="Could not track the branch",
        )


async def fetch(path: Path, branch: str, token: str | None) -> tuple[bool, str]:
    """Update ``origin/<branch>`` from the remote.

    The refspec is explicit because a single-branch clone only maps the branch
    it was cloned with: fetching any other one would leave its remote-tracking
    ref stale, and ahead/behind with it.
    """
    branch = check_branch(branch)
    await _track(path, branch)
    code, out, err = await _run_git(
        ["fetch", "origin", f"+refs/heads/{branch}:{_tracking_ref(branch)}"],
        cwd=path,
        token=token,
    )
    if code == 0:
        return True, (err or out).strip() or "Up to date."
    return False, (err or out).strip()


async def pull(path: Path, branch: str, token: str | None) -> tuple[bool, str]:
    """Fetch, then fast-forward only.

    Returns ``(ok, detail)``. ``ok=False`` means the branch has diverged or a
    conflict exists and the user must resolve it with the git CLI. A failed
    fetch raises ``GitError``: that is a network or auth problem, not a merge.
    """
    fetched, detail = await fetch(path, branch, token)
    if not fetched:
        raise GitError(f"Fetch failed: {detail}")
    code, out, err = await _run_git(["merge", "--ff-only", _tracking_ref(branch)], cwd=path)
    if code == 0:
        return True, (out or err).strip() or "Up to date."
    return False, (err or out).strip()


async def commit_all(path: Path, message: str) -> tuple[bool, str]:
    """Stage every change and commit. Returns ``(committed, detail)``."""
    await _git(["add", "-A"], cwd=path, what="Staging failed")
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
    requested = list(dict.fromkeys(check_path(p) for p in paths))
    changed = {f.path: f for f in (await status(path)).files}
    # A path with nothing to commit (a stale selection) is left out.
    selected = [p for p in requested if p in changed]
    if not selected:
        return False, "Nothing to commit."
    # Only what has unstaged changes needs `add` (which fails on a path that is
    # neither in the index nor on disk, e.g. an already staged deletion).
    unstaged = [p for p in selected if changed[p].code[1] != " "]
    # A staged rename commits whole: its old path goes along.
    renamed = [o for p in selected if (o := changed[p].orig_path)]
    paths = list(dict.fromkeys([*selected, *renamed]))
    if unstaged:
        await _git(["add", "-A", "--", *unstaged], cwd=path, what="Staging failed")
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
    rel = check_path(rel)
    _c, st_out, _e = await _run_git(["status", "--porcelain", "--", rel], cwd=path)
    code_part = st_out[:2] if st_out else ""
    if code_part.strip() == "??":
        # Untracked: diff against the empty blob via the no-index mode.
        # ``--no-index`` returns exit 1 when the files differ (expected here).
        _code, out, _err = await _run_git(["diff", "--no-index", "--", "/dev/null", rel], cwd=path)
        return out, "Binary files" in out
    _code, out, _err = await _run_git(["diff", "HEAD", "--", rel], cwd=path)
    return out, "Binary files" in out


# Past this, a diff isn't worth sending to the browser.
MAX_DIFF_BYTES = 2_000_000


class _TooLarge(Exception):
    pass


async def _blob(path: Path, rev: str, rel: str) -> bytes | None:
    """``rel`` as commit ``rev`` has it; None when it doesn't have it."""
    spec = f"{rev}:{rel}"
    code, out, _err = await _run_git(["cat-file", "-s", spec], cwd=path)
    if code != 0:
        return None
    if int(out.strip() or 0) > MAX_DIFF_BYTES:
        raise _TooLarge
    code, data, err = await _run_git_bytes(["cat-file", "blob", spec], cwd=path)
    if code != 0:
        raise GitError(f"Could not read {spec}: {err.strip()}")
    return data


def _working_file(path: Path, rel: str) -> bytes | None:
    target = fs.safe_join(path, rel)
    if not target.is_file():
        return None
    if target.stat().st_size > MAX_DIFF_BYTES:
        raise _TooLarge
    return target.read_bytes()


def _text(data: bytes | None) -> str | None:
    """Text for the diff editor; raises ``UnicodeDecodeError`` on binary data."""
    if data is None:
        return None
    if b"\0" in data[:8000]:
        raise UnicodeDecodeError("utf-8", data, 0, 1, "binary")
    return data.decode("utf-8")


async def file_versions(
    path: Path,
    rel: str,
    *,
    original_rel: str | None = None,
    base: str = "HEAD",
    head: str | None = None,
) -> FileVersions:
    """Both sides of one file's diff, for a side-by-side view.

    The original is ``rel`` (or its pre-rename ``original_rel``) at ``base``;
    the modified side is the working copy, or ``rel`` at ``head``. A side the
    file doesn't exist on is null.
    """
    rel = check_path(rel)
    original_rel = check_path(original_rel) if original_rel else rel
    base = check_rev(base)
    head = check_rev(head) if head else None
    try:
        before = await _blob(path, base, original_rel)
        after = await _blob(path, head, rel) if head else _working_file(path, rel)
    except _TooLarge:
        return FileVersions(path=rel, too_large=True)
    try:
        return FileVersions(path=rel, original=_text(before), modified=_text(after))
    except UnicodeDecodeError:
        return FileVersions(path=rel, binary=True)


def push_rejected(detail: str) -> bool:
    """Whether a failed push needs the remote's commits first (vs. auth/network)."""
    return any(marker in detail for marker in _PUSH_REJECTED)


async def push(path: Path, branch: str, token: str | None) -> tuple[bool, str]:
    """Push ``branch`` to ``origin``; publish it (``-u``) when it has no upstream."""
    branch = check_branch(branch)
    published = (await status(path)).upstream is not None
    await _track(path, branch)
    args = ["push", "origin", f"refs/heads/{branch}:refs/heads/{branch}"]
    if not published:
        args.insert(1, "--set-upstream")
    code, out, err = await _run_git(args, cwd=path, token=token)
    if code == 0:
        if not published:
            return True, f"Published {branch} to origin."
        return True, (out or err).strip() or "Pushed."
    return False, (err or out).strip()


async def _in_head(path: Path, rel: str) -> bool:
    code, _out, _err = await _run_git(["cat-file", "-e", f"HEAD:{rel}"], cwd=path)
    return code == 0


async def discard(path: Path, rel: str) -> None:
    """Bring ``rel`` back to its HEAD state, staged changes included.

    A file HEAD doesn't have (untracked, or added) is deleted; a rename also
    restores its old name.
    """
    rel = check_path(rel)
    entry = next((f for f in (await status(path)).files if f.path == rel), None)
    targets = [rel] + ([entry.orig_path] if entry is not None and entry.orig_path else [])
    for target in targets:
        if await _in_head(path, target):
            await _git(
                ["restore", "--source=HEAD", "--staged", "--worktree", "--", target],
                cwd=path,
                what=f"Could not discard {target}",
            )
            continue
        await _git(
            ["rm", "--cached", "--quiet", "--ignore-unmatch", "--", target],
            cwd=path,
            what=f"Could not discard {target}",
        )
        # Resolve the folder, not the file: the entry itself may be a symlink.
        parent = PurePosixPath(target).parent.as_posix()
        folder = fs.safe_join(path, "" if parent == "." else parent)
        file = folder / PurePosixPath(target).name
        if file.is_symlink() or file.is_file():
            file.unlink()


async def status(path: Path, subdir: str | None = None) -> GitStatus:
    """The working copy's branch, upstream, ahead/behind and changed files.

    ``subdir`` is the part of the repository the file browser shows; each
    file's ``browse_path`` is relative to it (null when outside it).
    """
    out = await _git(
        ["status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all"],
        cwd=path,
        what="git status failed",
    )
    prefix = f"{subdir.strip('/')}/" if subdir and subdir.strip("/") else ""
    head = "HEAD"
    upstream: str | None = None
    ahead: int | None = None
    behind: int | None = None
    files: list[GitFileStatus] = []

    fields = out.split("\0")
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if not entry:
            continue
        if entry.startswith("# "):
            key, _, value = entry[2:].partition(" ")
            if key == "branch.head":
                head = value
            elif key == "branch.upstream":
                upstream = value
            elif key == "branch.ab":
                plus, minus = value.split()
                ahead, behind = int(plus.lstrip("+")), int(minus.lstrip("-"))
            continue
        kind = entry[0]
        orig: str | None = None
        if kind == "1":
            parts = entry.split(" ", 8)
            xy, name = parts[1], parts[8]
        elif kind == "2":
            parts = entry.split(" ", 9)
            xy, name = parts[1], parts[9]
            # A rename's old path is the next NUL-separated field.
            orig = fields[i] if i < len(fields) else None
            i += 1
        elif kind == "u":
            parts = entry.split(" ", 10)
            xy, name = parts[1], parts[10]
        elif kind == "?":
            xy, name = "??", entry[2:]
        else:
            continue
        # With no subdir the prefix is "", which every path starts with.
        browse = name.removeprefix(prefix) if name.startswith(prefix) else None
        files.append(
            GitFileStatus(
                path=name,
                code=xy.replace(".", " "),
                orig_path=orig,
                conflicted=kind == "u",
                browse_path=browse,
            )
        )

    if upstream is not None and not upstream.startswith("origin/"):
        # Tracking a local branch (`branch.autoSetupMerge=always` does that for
        # every new branch): not published, and ahead/behind would compare
        # with the wrong thing.
        upstream = ahead = behind = None
    detached = head == "(detached)"
    merging_code, _o, _e = await _run_git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=path)
    return GitStatus(
        branch="HEAD" if detached else head,
        detached=detached,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        dirty=bool(files),
        merging=merging_code == 0,
        files=files,
    )
