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

from precursor.backend.schemas.workspace import (
    FileVersions,
    GitBranch,
    GitBranches,
    GitCommit,
    GitCommitDetail,
    GitCommitFile,
    GitConflict,
    GitFileStatus,
    GitLog,
    GitStatus,
)
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


class GitNotFound(GitError):
    """A well-formed revision this repository doesn't have."""


class GitRefused(GitError):
    """An operation that would lose work or can't run in the current state."""


def git_available() -> bool:
    return shutil.which("git") is not None


# --- Input checks ---------------------------------------------------------------


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def check_path(rel: str) -> str:
    """A working-tree path relative to the repository root, normalised.

    Checked on its text rather than resolved: git addresses the path itself (a
    symlink, a deleted file), and it refuses to follow a symlinked directory.
    """
    text = rel or ""
    # Refused, not stripped: a name must reach git spelled as it was sent.
    if not text or text != text.strip() or _CONTROL.search(text) or "\\" in text:
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
    # Not stripped: whitespace and control characters are refused below.
    branch = name or ""
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
    value = rev or ""
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
    base: str | None = "HEAD",
    head: str | None = None,
) -> FileVersions:
    """Both sides of one file's diff, for a side-by-side view.

    The original is ``rel`` (or its pre-rename ``original_rel``) at ``base``;
    the modified side is the working copy, or ``rel`` at ``head``. A side the
    file doesn't exist on is null; so is the original when ``base`` is None
    (a root commit has nothing before it).
    """
    rel = check_path(rel)
    original_rel = check_path(original_rel) if original_rel else rel
    base = check_rev(base) if base else None
    head = check_rev(head) if head else None
    try:
        before = await _blob(path, base, original_rel) if base else None
        after = await _blob(path, head, rel) if head else _working_file(path, rel)
    except _TooLarge:
        return FileVersions(path=rel, too_large=True)
    try:
        return FileVersions(path=rel, original=_text(before), modified=_text(after))
    except UnicodeDecodeError:
        return FileVersions(path=rel, binary=True)


# --- History ------------------------------------------------------------------------

MAX_LOG_PAGE = 100
# Fields of one commit, split on the unit separator (never in names/subjects).
_LOG_FORMAT = "%H%x1f%h%x1f%an%x1f%aI%x1f%P%x1f%s"


def _commit(fields: list[str]) -> GitCommit:
    sha, short, author, date, parents, subject = fields[:6]
    return GitCommit(
        sha=sha,
        short_sha=short,
        author=author,
        date=date,
        subject=subject,
        parents=parents.split(),
    )


async def log(path: Path, *, limit: int = 50, skip: int = 0, rel: str | None = None) -> GitLog:
    """A page of the checked-out branch's history, newest first.

    With ``rel``, only the commits that touched that file, following it across
    renames.
    """
    limit = max(1, min(limit, MAX_LOG_PAGE))
    skip = max(0, skip)
    args = ["log", f"--format={_LOG_FORMAT}", "-z", f"--max-count={limit + 1}", f"--skip={skip}"]
    if rel:
        args += ["--follow", "--", check_path(rel)]
    code, out, err = await _run_git(args, cwd=path)
    if code != 0:
        if "does not have any commits" in err:
            return GitLog()
        raise GitError(f"git log failed: {err.strip()}", stderr=err)
    commits = [_commit(entry.split("\x1f")) for entry in out.split("\0") if entry.strip()]
    return GitLog(commits=commits[:limit], has_more=len(commits) > limit)


async def commit_detail(path: Path, rev: str) -> GitCommitDetail:
    """One commit: its message and the files it changed against its first parent."""
    rev = check_rev(rev)
    code, sha, _err = await _run_git(
        ["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], cwd=path
    )
    if code != 0 or not sha.strip():
        raise GitNotFound(f"No commit {rev!r} in this repository")
    sha = sha.strip()
    out = await _git(
        ["show", "-s", "-z", f"--format={_LOG_FORMAT}%x1f%b", sha],
        cwd=path,
        what="git show failed",
    )
    fields = out.rstrip("\0").split("\x1f")
    commit = _commit(fields)
    body = fields[6].strip() if len(fields) > 6 else ""
    parent = commit.parents[0] if commit.parents else None
    # A merge is compared with its first parent, like `git show --first-parent`;
    # a root commit with nothing (every file added).
    args = ["diff-tree", "-r", "-z", "-M", "--name-status", "--no-commit-id"]
    args += [parent, sha] if parent else ["--root", sha]
    raw = (await _git(args, cwd=path, what="git diff-tree failed")).split("\0")
    files: list[GitCommitFile] = []
    i = 0
    while i < len(raw) and raw[i]:
        status = raw[i]
        if status[0] in "RC":
            files.append(GitCommitFile(status=status[0], orig_path=raw[i + 1], path=raw[i + 2]))
            i += 3
        else:
            files.append(GitCommitFile(status=status[0], path=raw[i + 1]))
            i += 2
    return GitCommitDetail(**commit.model_dump(), body=body, parent=parent, files=files)


# --- Branches -------------------------------------------------------------------------


async def _local_branches(path: Path) -> list[GitBranch]:
    out = await _git(
        ["for-each-ref", "--format=%(refname:short)%09%(upstream:short)", "refs/heads"],
        cwd=path,
        what="Could not list branches",
    )
    local: list[GitBranch] = []
    for line in out.splitlines():
        name, _, upstream = line.partition("\t")
        try:
            check_branch(name)
        except GitInputError:
            continue
        # Only an upstream on origin counts as published (see status()).
        local.append(
            GitBranch(name=name, upstream=upstream if upstream.startswith("origin/") else None)
        )
    return local


async def remote_branches(path: Path, token: str | None) -> list[str]:
    """Branch names on ``origin``, asked of the remote itself.

    A single-branch clone knows only the branch it was cloned with, so the
    remote-tracking refs can't answer this. Any name ``check_branch`` refuses is
    left out: a remote can hold refs no one should type into git.
    """
    code, out, err = await _run_git(["ls-remote", "--heads", "origin"], cwd=path, token=token)
    if code != 0:
        raise GitError(f"Could not list the remote's branches: {(err or out).strip()}")
    names: list[str] = []
    for line in out.splitlines():
        _sha, _, ref = line.partition("\t")
        name = ref.removeprefix("refs/heads/")
        if name == ref:
            continue
        try:
            names.append(check_branch(name))
        except GitInputError:
            logger.warning("Ignoring a remote branch with an unusable name: %r", name)
    return sorted(names)


async def branches(path: Path, token: str | None) -> GitBranches:
    """Local branches, and the remote's (unreachable remote: local ones only)."""
    local = await _local_branches(path)
    remote: list[str] = []
    remote_error: str | None = None
    try:
        remote = await remote_branches(path, token)
    except GitError as exc:
        remote_error = str(exc)
    return GitBranches(
        current=await current_branch(path),
        local=local,
        remote=remote,
        remote_error=remote_error,
    )


async def _local_exists(path: Path, branch: str) -> bool:
    code, _out, _err = await _run_git(
        ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=path
    )
    return code == 0


def _names(paths: list[str], limit: int = 8) -> str:
    shown = ", ".join(paths[:limit])
    return shown + (f" and {len(paths) - limit} more" if len(paths) > limit else "")


def _overwritten(detail: str) -> list[str]:
    """The files git lists when a switch would overwrite them."""
    files: list[str] = []
    listing = False
    for line in detail.splitlines():
        if "would be overwritten" in line:
            listing = True
        elif listing and line.startswith("\t"):
            files.append(line.strip())
        elif listing:
            listing = False
    return files


async def _guard_switch(path: Path) -> GitStatus:
    """Refuse a switch that could lose work or can't run from here."""
    st = await status(path)
    if st.merging:
        raise GitRefused("A merge is in progress — finish or abort it first.")
    if st.detached:
        raise GitRefused("The working copy is on a detached HEAD — check out a branch first.")
    changed = [f.path for f in st.files if f.code != "??"]
    if changed:
        raise GitRefused(
            f"Commit or discard your changes before switching branches: {_names(changed)}."
        )
    return st


async def _run_switch(path: Path, args: list[str]) -> None:
    code, out, err = await _run_git(["switch", *args], cwd=path)
    if code == 0:
        return
    detail = (err or out).strip()
    blocked = _overwritten(detail)
    if blocked:
        # Untracked files the other branch has: git stops rather than overwrite.
        raise GitRefused(
            "These new files would be overwritten by the other branch's — "
            f"move or delete them first: {_names(blocked)}."
        )
    raise GitError(f"Could not switch: {detail}", stderr=err)


async def switch(path: Path, branch: str, token: str | None) -> None:
    """Check out ``branch``: a local one, or one only the remote has.

    Never forces, discards or stashes: any uncommitted change to a tracked file
    refuses the switch, and so do untracked files the branch would overwrite.
    """
    branch = check_branch(branch)
    await _guard_switch(path)
    if await _local_exists(path, branch):
        await _run_switch(path, ["--no-guess", branch])
        return
    if branch not in await remote_branches(path, token):
        raise GitNotFound(f"No branch named {branch!r} here or on the remote")
    fetched, detail = await fetch(path, branch, token)  # tracks it first (_track)
    if not fetched:
        raise GitError(f"Could not fetch {branch}: {detail}")
    await _run_switch(path, ["--no-guess", "--track", "-c", branch, f"origin/{branch}"])


_HEAD_LIKE = re.compile(r"[A-Z_]*HEAD")


async def _refuse_ambiguous(path: Path, branch: str) -> None:
    """Names git accepts for a branch but reads as something else elsewhere.

    ``refs/heads/x`` becomes ``refs/heads/refs/heads/x``; ``origin/main``
    shadows the remote-tracking branch; ``ORIG_HEAD`` a pseudo-ref.
    """
    prefixes = ["refs/", "heads/", "remotes/", "tags/"]
    code, out, _err = await _run_git(["remote"], cwd=path)
    remotes = out.split() if code == 0 else []
    prefixes += [f"{remote}/" for remote in {"origin", *remotes}]
    if _HEAD_LIKE.fullmatch(branch) or branch.startswith(tuple(prefixes)):
        raise GitInputError(
            f"{branch!r} would be confused with another kind of ref — choose another name."
        )


async def create_branch(path: Path, branch: str, token: str | None) -> None:
    """Create ``branch`` from HEAD and check it out; it stays unpublished.

    ``--no-track``: with ``branch.autoSetupMerge=always`` git would otherwise
    make it track the branch it started from. Uncommitted changes come along
    (HEAD doesn't move, so nothing is overwritten); a merge in progress refuses.
    """
    branch = check_branch(branch)
    await _refuse_ambiguous(path, branch)
    if (await status(path)).merging:
        raise GitRefused("A merge is in progress — finish or abort it first.")
    if await _local_exists(path, branch):
        raise GitRefused(f"A branch named {branch!r} already exists — switch to it instead.")
    try:
        on_remote = branch in await remote_branches(path, token)
    except GitError:
        # Offline: a local branch is still fine; publishing it will tell.
        on_remote = False
    if on_remote:
        raise GitRefused(
            f"The remote already has a branch named {branch!r} — switch to it instead."
        )
    await _run_switch(path, ["--no-track", "-c", branch])


# --- Merging ---------------------------------------------------------------------------

# The user's own settings must not change how a merge in the app behaves: no
# editor may open, no fast-forward replaces the merge commit, and conflict
# markers keep the two-sided style the editor understands.
_MERGE_CONFIG = [
    "-c",
    "core.editor=true",
    "-c",
    "merge.ff=false",
    "-c",
    "merge.conflictStyle=merge",
    "-c",
    "rerere.enabled=false",
]

# A line git writes around a conflict (``merge.conflictStyle=merge``).
_MARKER = re.compile(r"^(<{7}|={7}|>{7})(\s|$)", re.MULTILINE)

_CONFLICT_KINDS = {
    "UU": "both_modified",
    "AA": "both_added",
    "DU": "deleted_by_us",
    "UD": "deleted_by_them",
    "AU": "added_by_us",
    "UA": "added_by_them",
    "DD": "both_deleted",
}


async def merge(path: Path, branch: str, token: str | None) -> tuple[bool, str]:
    """Merge ``origin/<branch>`` into the checked-out branch, with a merge commit.

    Returns ``(clean, detail)``; ``clean=False`` leaves the merge in progress
    with the conflicted files marked in ``status()``. Refused (``GitRefused``)
    on a detached HEAD, during another merge, or with uncommitted changes to
    tracked files. Always ``git merge`` itself, never ``pull``: a user's
    ``branch.<b>.rebase`` or ``pull.rebase`` setting doesn't apply.
    """
    branch = check_branch(branch)
    st = await status(path)
    if st.merging:
        raise GitRefused("A merge is already in progress — complete or abort it first.")
    if st.detached:
        raise GitRefused("The working copy is on a detached HEAD — check out a branch first.")
    changed = [f.path for f in st.files if f.code != "??"]
    if changed:
        raise GitRefused(f"Commit or discard your changes before merging: {_names(changed)}.")
    fetched, detail = await fetch(path, branch, token)
    if not fetched:
        raise GitError(f"Fetch failed: {detail}")
    code, out, err = await _run_git(
        [*_MERGE_CONFIG, "merge", "--no-ff", "--no-edit", "--no-autostash", f"origin/{branch}"],
        cwd=path,
    )
    detail = (out + err).strip()
    if code == 0:
        return True, detail or f"Merged origin/{branch}."
    if (await status(path)).merging:
        conflicted = [f.path for f in (await status(path)).files if f.conflicted]
        return False, f"{len(conflicted)} file(s) conflict: {_names(conflicted)}."
    blocked = _overwritten(detail)
    if blocked:
        raise GitRefused(
            "These new files would be overwritten by the merge — "
            f"move or delete them first: {_names(blocked)}."
        )
    raise GitError(f"Merge failed: {detail}", stderr=err)


async def _stage(path: Path, stage: int, rel: str) -> bytes | None:
    spec = f":{stage}:{rel}"
    code, out, _err = await _run_git(["cat-file", "-s", spec], cwd=path)
    if code != 0:
        return None
    if int(out.strip() or 0) > MAX_DIFF_BYTES:
        raise _TooLarge
    code, data, err = await _run_git_bytes(["cat-file", "blob", spec], cwd=path)
    if code != 0:
        raise GitError(f"Could not read {spec}: {err.strip()}")
    return data


async def conflict(path: Path, rel: str) -> GitConflict:
    """One conflicted file: its kind and the base / ours / theirs versions.

    Each side may be absent: add/add has no base, delete/modify lacks one side.
    Binary or over 2 MB: flagged, without contents.
    """
    rel = check_path(rel)
    entry = next((f for f in (await status(path)).files if f.path == rel and f.conflicted), None)
    if entry is None:
        raise GitNotFound(f"{rel} has no conflict")
    kind = _CONFLICT_KINDS.get(entry.code, "both_modified")
    try:
        base, ours, theirs = [await _stage(path, n, rel) for n in (1, 2, 3)]
    except _TooLarge:
        return GitConflict(path=rel, kind=kind, too_large=True)
    present = {
        "has_base": base is not None,
        "has_ours": ours is not None,
        "has_theirs": theirs is not None,
    }
    try:
        return GitConflict(
            path=rel, kind=kind, base=_text(base), ours=_text(ours), theirs=_text(theirs), **present
        )
    except UnicodeDecodeError:
        return GitConflict(path=rel, kind=kind, binary=True, **present)


def conflict_marker_lines(text: str) -> list[int]:
    """1-based lines that are conflict markers."""
    return [text.count("\n", 0, m.start()) + 1 for m in _MARKER.finditer(text)]


async def resolve(path: Path, rel: str, side: str | None = None) -> None:
    """Mark a conflicted file resolved.

    Without ``side``: as it is in the working copy, which must hold no conflict
    marker any more. With ``ours`` / ``theirs``: that side's version, or the
    file's deletion when that side deleted it.
    """
    rel = check_path(rel)
    if side not in (None, "ours", "theirs"):
        raise GitInputError(f"Invalid side: {side!r}")
    entry = next((f for f in (await status(path)).files if f.path == rel), None)
    if entry is None or not entry.conflicted:
        raise GitRefused(f"{rel} has no conflict to resolve.")
    if side is None:
        target = fs.safe_join(path, rel)
        if target.is_file():
            try:
                lines = conflict_marker_lines(target.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                raise GitRefused(
                    f"{rel} is binary: keep one side (ours or theirs) instead."
                ) from None
            if lines:
                shown = ", ".join(str(n) for n in lines[:6])
                raise GitRefused(
                    f"Conflict markers remain in {rel} (line {shown}). "
                    "Resolve them and save before marking it resolved."
                )
        await _git(["add", "-A", "--", rel], cwd=path, what=f"Could not resolve {rel}")
        return
    stage = 2 if side == "ours" else 3
    code, _out, _err = await _run_git(["cat-file", "-e", f":{stage}:{rel}"], cwd=path)
    if code == 0:
        await _git(["checkout", f"--{side}", "--", rel], cwd=path, what=f"Could not keep {side}")
        await _git(["add", "--", rel], cwd=path, what=f"Could not resolve {rel}")
    else:
        # That side deleted the file.
        await _git(["rm", "--quiet", "--", rel], cwd=path, what=f"Could not resolve {rel}")


async def complete_merge(path: Path) -> str:
    """Commit the merge once no file is conflicted any more."""
    st = await status(path)
    if not st.merging:
        raise GitRefused("No merge is in progress.")
    left = [f.path for f in st.files if f.conflicted]
    if left:
        raise GitRefused(f"Resolve every conflict first: {_names(left)}.")
    out = await _git(
        [*_MERGE_CONFIG, "commit", "--no-edit"], cwd=path, what="Could not complete the merge"
    )
    return out.strip() or "Merge completed."


async def abort_merge(path: Path) -> None:
    """Put the branch and files back as they were before the merge."""
    if not (await status(path)).merging:
        raise GitRefused("No merge is in progress.")
    await _git(["merge", "--abort"], cwd=path, what="Could not abort the merge")


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
    oid: str | None = None
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
            if key == "branch.oid":
                oid = None if value == "(initial)" else value
            elif key == "branch.head":
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
        head=oid,
        detached=detached,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        dirty=bool(files),
        merging=merging_code == 0,
        files=files,
    )
