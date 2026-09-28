"""Workspace git operations against a real (temporary) remote.

The remote is a bare repository reached through a ``file://`` URL
(``Path.as_uri()``, so it also works on Windows). Git runs with an empty global
config and a fixed identity, so the developer's own settings can't leak in.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from precursor.backend.services import workspace_git as git


def run(cwd: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=check, capture_output=True, text=True
    ).stdout


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "gitconfig"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


@pytest.fixture
def remote(tmp_path: Path) -> str:
    """A bare remote whose ``main`` has one commit (README.md)."""
    bare = tmp_path / "remote.git"
    run(tmp_path, "init", "--bare", "--initial-branch=main", str(bare))
    seed = tmp_path / "seed"
    run(tmp_path, "init", "--initial-branch=main", str(seed))
    (seed / "README.md").write_text("# Notes\n\nfirst line\n", encoding="utf-8")
    run(seed, "add", "-A")
    run(seed, "commit", "-m", "Initial")
    run(seed, "remote", "add", "origin", bare.as_uri())
    run(seed, "push", "origin", "main")
    return bare.as_uri()


async def _clone(remote: str, dest: Path) -> Path:
    await git.clone(remote, dest, "main", None)
    return dest


def _other_clone(remote: str, tmp_path: Path, name: str = "other") -> Path:
    dest = tmp_path / name
    run(tmp_path, "clone", remote, str(dest))
    return dest


def _commit_and_push(repo: Path, rel: str, text: str, message: str) -> None:
    (repo / rel).write_text(text, encoding="utf-8")
    run(repo, "add", "-A")
    run(repo, "commit", "-m", message)
    run(repo, "push", "origin", "HEAD")


# --- Input checks ----------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["", "../x", "a/../../x", "/etc/passwd", ".git/config", "sub/.git/HEAD", "C:/x", "a\\b"],
)
def test_check_path_rejects_escapes_and_git_internals(bad: str) -> None:
    with pytest.raises(git.GitInputError):
        git.check_path(bad)


def test_check_path_normalises() -> None:
    assert git.check_path("./docs//a b.md") == "docs/a b.md"
    assert git.check_path(":/") == ":"  # a (literal) file named ":"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "-rf",
        "HEAD",
        "@",
        "a..b",
        "a:b",
        "a b",
        "a~1",
        "a^",
        "@{-1}",
        "a/",
        "/a",
        ".a",
        "a.lock",
        "a//b",
        "x.",
    ],
)
def test_check_branch_rejects_what_git_would(bad: str) -> None:
    with pytest.raises(git.GitInputError):
        git.check_branch(bad)


def test_check_branch_accepts_ordinary_names() -> None:
    for good in ("main", "feature/monaco", "fix-123", "release/2026.9"):
        assert git.check_branch(good) == good


def test_check_rev() -> None:
    assert git.check_rev("HEAD") == "HEAD"
    assert git.check_rev("a1b2c3d") == "a1b2c3d"
    for bad in ("HEAD~1", "main", "--all", "a1b2c3d^", ""):
        with pytest.raises(git.GitInputError):
            git.check_rev(bad)


def test_git_never_prompts_and_takes_paths_literally() -> None:
    env = git.git_env()
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_LITERAL_PATHSPECS"] == "1"


# --- Status ----------------------------------------------------------------------


async def test_status_parses_edits_untracked_unicode_spaces_and_renames(
    remote: str, tmp_path: Path
) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    (repo / "README.md").write_text("# Notes\n\nedited\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "a b.md").write_text("spaces\n", encoding="utf-8")
    (repo / "ünï.md").write_text("unicode\n", encoding="utf-8")
    (repo / "old.md").write_text("to rename\n", encoding="utf-8")
    run(repo, "add", "old.md")
    run(repo, "commit", "-m", "Add old")
    run(repo, "mv", "old.md", "new name.md")

    st = await git.status(repo)
    by_path = {f.path: f for f in st.files}
    assert st.branch == "main" and not st.detached
    assert st.upstream == "origin/main"
    assert (st.ahead, st.behind) == (1, 0)
    assert by_path["README.md"].code == " M"
    assert by_path["docs/a b.md"].code == "??"  # listed per file, not as "docs/"
    assert by_path["ünï.md"].code == "??"
    renamed = by_path["new name.md"]
    assert renamed.code == "R " and renamed.orig_path == "old.md"
    assert all(f.browse_path == f.path for f in st.files)
    assert st.dirty and not st.merging


async def test_status_browse_paths_follow_the_subdir(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.md").write_text("x\n", encoding="utf-8")
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    st = await git.status(repo, "docs/")
    by_path = {f.path: f.browse_path for f in st.files}
    assert by_path == {"docs/guide.md": "guide.md", "README.md": None}


async def test_status_reports_a_conflicted_merge(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    other = _other_clone(remote, tmp_path)
    _commit_and_push(other, "README.md", "# Notes\n\ntheirs\n", "Theirs")
    (repo / "README.md").write_text("# Notes\n\nours\n", encoding="utf-8")
    run(repo, "commit", "-am", "Ours")
    assert (await git.fetch(repo, "main", None))[0]
    subprocess.run(["git", "merge", "origin/main"], cwd=repo, capture_output=True)

    st = await git.status(repo)
    [readme] = st.files
    assert readme.conflicted and readme.code == "UU"
    assert st.merging


async def test_a_detached_head_has_no_branch(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    run(repo, "checkout", "--detach")
    st = await git.status(repo)
    assert (st.branch, st.detached, st.upstream) == ("HEAD", True, None)
    assert await git.current_branch(repo) is None


async def test_status_of_a_missing_working_copy_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(git.GitError):
        await git.status(tmp_path / "gone")


# --- Fetch, commit, push ---------------------------------------------------------


async def test_fetch_shows_what_the_remote_has(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    _commit_and_push(_other_clone(remote, tmp_path), "b.md", "b\n", "Add b")
    assert (await git.status(repo)).behind == 0
    ok, _detail = await git.fetch(repo, "main", None)
    assert ok
    st = await git.status(repo)
    assert (st.ahead, st.behind) == (0, 1)
    assert not (repo / "b.md").exists()  # fetch changes no file


async def test_fetch_maps_a_branch_the_single_branch_clone_doesnt_track(
    remote: str, tmp_path: Path
) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    other = _other_clone(remote, tmp_path)
    run(other, "switch", "-c", "feature")
    _commit_and_push(other, "f.md", "f\n", "Feature")
    ok, _ = await git.fetch(repo, "feature", None)
    assert ok
    assert (
        run(repo, "rev-parse", "origin/feature").strip() == run(other, "rev-parse", "HEAD").strip()
    )


async def test_fetch_of_a_branch_the_remote_lacks_fails_softly(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    ok, detail = await git.fetch(repo, "nope", None)
    assert not ok and detail


async def test_commit_then_push(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    (repo / "a.md").write_text("a\n", encoding="utf-8")
    (repo / "keep.md").write_text("not yet\n", encoding="utf-8")
    committed, _ = await git.commit_paths(repo, "Add a", ["a.md"])
    assert committed
    st = await git.status(repo)
    assert st.ahead == 1
    assert [f.path for f in st.files] == ["keep.md"]  # left for later

    ok, _ = await git.push(repo, "main", None)
    assert ok
    st = await git.status(repo)
    assert (st.ahead, st.behind) == (0, 0)


async def test_committing_a_rename_takes_its_old_path_along(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    run(repo, "mv", "README.md", "INTRO.md")
    committed, _ = await git.commit_paths(repo, "Rename", ["INTRO.md"])
    assert committed
    assert (await git.status(repo)).files == []
    assert "README.md" not in run(repo, "ls-tree", "--name-only", "HEAD")


async def test_nothing_to_commit(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    assert await git.commit_all(repo, "Nothing") == (False, "Nothing to commit.")


async def test_push_publishes_a_new_branch(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    run(repo, "switch", "-c", "draft")
    (repo / "d.md").write_text("d\n", encoding="utf-8")
    await git.commit_all(repo, "Draft")
    st = await git.status(repo)
    assert (st.branch, st.upstream, st.ahead) == ("draft", None, None)

    ok, detail = await git.push(repo, "draft", None)
    assert ok and "Published draft" in detail
    st = await git.status(repo)
    assert (st.upstream, st.ahead, st.behind) == ("origin/draft", 0, 0)


async def test_a_branch_tracking_a_local_one_is_not_published(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    run(repo, "config", "branch.autoSetupMerge", "always")
    run(repo, "switch", "-c", "draft")  # now tracks the local `main`
    st = await git.status(repo)
    assert (st.upstream, st.ahead, st.behind) == (None, None, None)
    ok, _ = await git.push(repo, "draft", None)
    assert ok and (await git.status(repo)).upstream == "origin/draft"


async def test_a_push_behind_the_remote_is_rejected(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    _commit_and_push(_other_clone(remote, tmp_path), "b.md", "b\n", "Theirs")
    (repo / "a.md").write_text("a\n", encoding="utf-8")
    await git.commit_all(repo, "Ours")
    ok, detail = await git.push(repo, "main", None)
    assert not ok and git.push_rejected(detail)


async def test_pull_fast_forwards(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    _commit_and_push(_other_clone(remote, tmp_path), "b.md", "b\n", "Theirs")
    ok, _ = await git.pull(repo, "main", None)
    assert ok and (repo / "b.md").read_text(encoding="utf-8") == "b\n"


async def test_a_diverged_pull_stops(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    _commit_and_push(_other_clone(remote, tmp_path), "b.md", "b\n", "Theirs")
    (repo / "a.md").write_text("a\n", encoding="utf-8")
    await git.commit_all(repo, "Ours")
    ok, detail = await git.pull(repo, "main", None)
    assert not ok and detail
    assert not (repo / "b.md").exists()


# --- Discard and path safety -----------------------------------------------------


async def test_discard_restores_tracked_and_removes_new_files(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    (repo / "README.md").write_text("edited\n", encoding="utf-8")
    run(repo, "add", "README.md")  # staged edits are discarded too
    (repo / "new.md").write_text("new\n", encoding="utf-8")
    await git.discard(repo, "README.md")
    await git.discard(repo, "new.md")
    assert (repo / "README.md").read_text(encoding="utf-8") == "# Notes\n\nfirst line\n"
    assert not (repo / "new.md").exists()
    assert (await git.status(repo)).files == []


async def test_discarding_a_rename_restores_the_old_name(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    run(repo, "mv", "README.md", "INTRO.md")
    await git.discard(repo, "INTRO.md")
    assert (repo / "README.md").exists() and not (repo / "INTRO.md").exists()
    assert (await git.status(repo)).files == []


async def test_pathspec_magic_is_literal(remote: str, tmp_path: Path) -> None:
    """``:/`` or ``*`` would mean "everything" to git; here each is just a name."""
    repo = await _clone(remote, tmp_path / "ws")
    (repo / "README.md").write_text("keep me\n", encoding="utf-8")
    for magic in (":/", ":(top)README.md", "*", "*.md"):
        await git.discard(repo, magic)
        assert await git.commit_paths(repo, "m", [magic]) == (False, "Nothing to commit.")
    assert (repo / "README.md").read_text(encoding="utf-8") == "keep me\n"
    assert [f.path for f in (await git.status(repo)).files] == ["README.md"]
    with pytest.raises(git.GitInputError):
        await git.discard(repo, "../outside.md")
    with pytest.raises(git.GitInputError):
        await git.diff_file(repo, ".git/config")


# --- File versions (the diff editor) -------------------------------------------------


async def test_file_versions_of_an_edit_a_new_file_and_a_deletion(
    remote: str, tmp_path: Path
) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    (repo / "README.md").write_text("# Notes\n\nedited\n", encoding="utf-8")
    (repo / "new.md").write_text("new\n", encoding="utf-8")
    edited = await git.file_versions(repo, "README.md")
    assert (edited.original, edited.modified) == ("# Notes\n\nfirst line\n", "# Notes\n\nedited\n")
    added = await git.file_versions(repo, "new.md")
    assert (added.original, added.modified) == (None, "new\n")
    (repo / "README.md").unlink()
    deleted = await git.file_versions(repo, "README.md")
    assert (deleted.original is not None, deleted.modified) == (True, None)


async def test_file_versions_of_a_rename_compare_with_the_old_name(
    remote: str, tmp_path: Path
) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    run(repo, "mv", "README.md", "INTRO.md")
    versions = await git.file_versions(repo, "INTRO.md", original_rel="README.md")
    assert versions.original == versions.modified == "# Notes\n\nfirst line\n"


async def test_file_versions_between_two_commits(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    first = run(repo, "rev-parse", "HEAD").strip()
    (repo / "README.md").write_text("second\n", encoding="utf-8")
    run(repo, "commit", "-am", "Second")
    second = run(repo, "rev-parse", "HEAD").strip()
    (repo / "README.md").write_text("uncommitted\n", encoding="utf-8")
    versions = await git.file_versions(repo, "README.md", base=first[:7], head=second)
    assert (versions.original, versions.modified) == ("# Notes\n\nfirst line\n", "second\n")


async def test_file_versions_flag_binary_and_large_files(
    remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    (repo / "logo.png").write_bytes(b"\x89PNG\0\0\x01")
    binary = await git.file_versions(repo, "logo.png")
    assert binary.binary and binary.modified is None
    monkeypatch.setattr(git, "MAX_DIFF_BYTES", 10)
    (repo / "README.md").write_text("x" * 11, encoding="utf-8")
    large = await git.file_versions(repo, "README.md")
    assert large.too_large and large.original is None and large.modified is None


async def test_file_versions_check_their_inputs(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    for kwargs in ({"base": "HEAD~1"}, {"head": "--all"}, {"original_rel": "../x"}):
        with pytest.raises(git.GitInputError):
            await git.file_versions(repo, "README.md", **kwargs)  # type: ignore[arg-type]
    with pytest.raises(git.GitInputError):
        await git.file_versions(repo, ".git/config")


# --- History ---------------------------------------------------------------------


async def _history(repo: Path) -> dict[str, str]:
    """Four more commits on the clone: an edit, a rename, a merge. Returns their ids."""
    ids: dict[str, str] = {}
    (repo / "README.md").write_text("# Notes\n\nsecond line\n", encoding="utf-8")
    run(repo, "commit", "-am", "Edit the notes", "-m", "Why: clearer.")
    ids["edit"] = run(repo, "rev-parse", "HEAD").strip()
    run(repo, "mv", "README.md", "NOTES.md")
    run(repo, "commit", "-m", "Rename the notes")
    ids["rename"] = run(repo, "rev-parse", "HEAD").strip()
    run(repo, "switch", "-c", "side")
    (repo / "side.md").write_text("side\n", encoding="utf-8")
    run(repo, "add", "side.md")
    run(repo, "commit", "-m", "Side work")
    run(repo, "switch", "main")
    (repo / "main.md").write_text("main\n", encoding="utf-8")
    run(repo, "add", "main.md")
    run(repo, "commit", "-m", "Main work")
    run(repo, "merge", "--no-ff", "--no-edit", "side")
    ids["merge"] = run(repo, "rev-parse", "HEAD").strip()
    ids["root"] = run(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    return ids


async def test_log_pages_newest_first(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    ids = await _history(repo)
    first = await git.log(repo, limit=2)
    assert first.commits[0].sha == ids["merge"] and first.has_more
    assert len(first.commits[0].parents) == 2  # a merge
    rest = await git.log(repo, limit=100, skip=2)
    assert not rest.has_more and rest.commits[-1].sha == ids["root"]
    assert rest.commits[-1].parents == []
    assert len(first.commits) + len(rest.commits) == 6


async def test_log_of_a_file_follows_its_renames(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    ids = await _history(repo)
    history = await git.log(repo, rel="NOTES.md")
    assert [c.sha for c in history.commits] == [ids["rename"], ids["edit"], ids["root"]]
    with pytest.raises(git.GitInputError):
        await git.log(repo, rel="../x")


async def test_log_of_an_empty_repository(tmp_path: Path) -> None:
    run(tmp_path, "init", "--initial-branch=main", "empty")
    assert await git.log(tmp_path / "empty") == git.GitLog()


async def test_commit_detail_of_an_edit_a_rename_a_merge_and_the_root(
    remote: str, tmp_path: Path
) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    ids = await _history(repo)

    edit = await git.commit_detail(repo, ids["edit"][:8])
    assert edit.sha == ids["edit"] and edit.subject == "Edit the notes"
    assert edit.body == "Why: clearer."
    assert edit.parent == ids["root"]
    assert [(f.status, f.path) for f in edit.files] == [("M", "README.md")]

    rename = await git.commit_detail(repo, ids["rename"])
    [moved] = rename.files
    assert (moved.status, moved.orig_path, moved.path) == ("R", "README.md", "NOTES.md")

    merge = await git.commit_detail(repo, ids["merge"])
    assert merge.parent == merge.parents[0]
    # Against its first parent: only what the merged branch brought in.
    assert [(f.status, f.path) for f in merge.files] == [("A", "side.md")]

    root = await git.commit_detail(repo, ids["root"])
    assert root.parent is None and root.parents == []
    assert [(f.status, f.path) for f in root.files] == [("A", "README.md")]


async def test_commit_detail_checks_its_input(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    for bad in ("HEAD^", "main", "--all"):
        with pytest.raises(git.GitInputError):
            await git.commit_detail(repo, bad)
    with pytest.raises(git.GitNotFound):
        await git.commit_detail(repo, "deadbeef")


async def test_file_versions_of_a_commit_use_the_resolved_parent(
    remote: str, tmp_path: Path
) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    ids = await _history(repo)
    edit = await git.commit_detail(repo, ids["edit"])
    versions = await git.file_versions(repo, "README.md", base=edit.parent, head=edit.sha)
    assert (versions.original, versions.modified) == (
        "# Notes\n\nfirst line\n",
        "# Notes\n\nsecond line\n",
    )
    root = await git.file_versions(repo, "README.md", base=None, head=ids["root"])
    assert (root.original, root.modified) == (None, "# Notes\n\nfirst line\n")


# --- Branches ------------------------------------------------------------------------


def _remote_branch(remote: str, tmp_path: Path, name: str, file: str) -> None:
    """Push a branch to the remote from another clone."""
    other = _other_clone(remote, tmp_path, f"other-{name.replace('/', '-')}")
    run(other, "switch", "-c", name)
    _commit_and_push(other, file, f"{name}\n", f"Work on {name}")


async def test_branches_lists_local_and_remote(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    _remote_branch(remote, tmp_path, "feature/x", "x.md")
    listed = await git.branches(repo, None)
    assert listed.current == "main"
    assert [(b.name, b.upstream) for b in listed.local] == [("main", "origin/main")]
    assert listed.remote == ["feature/x", "main"] and listed.remote_error is None


async def test_unusable_remote_branch_names_are_not_listed(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    bare = Path(remote.removeprefix("file://"))
    sha = run(tmp_path, f"--git-dir={bare}", "rev-parse", "main").strip()
    # A name git itself accepts but no one should pass to git on a command line.
    run(tmp_path, f"--git-dir={bare}", "update-ref", "refs/heads/--upload-pack=pwned", sha)
    (bare / "refs" / "heads" / "a..b").write_text(sha + "\n", encoding="utf-8")
    assert await git.remote_branches(repo, None) == ["main"]
    with pytest.raises(git.GitInputError):
        await git.switch(repo, "--upload-pack=pwned", None)
    assert not (repo / "pwned").exists()


async def test_switching_to_a_branch_only_the_remote_has(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")  # single-branch: knows only main
    _remote_branch(remote, tmp_path, "feature", "f.md")
    await git.switch(repo, "feature", None)
    st = await git.status(repo)
    assert (st.branch, st.upstream, st.ahead, st.behind) == ("feature", "origin/feature", 0, 0)
    assert (repo / "f.md").read_text(encoding="utf-8") == "feature\n"
    await git.switch(repo, "main", None)
    assert not (repo / "f.md").exists() and await git.current_branch(repo) == "main"


async def test_switching_is_refused_with_uncommitted_changes(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    run(repo, "branch", "other")
    (repo / "README.md").write_text("edited\n", encoding="utf-8")
    with pytest.raises(git.GitRefused, match=r"README\.md"):
        await git.switch(repo, "other", None)
    run(repo, "add", "README.md")  # staged counts too
    with pytest.raises(git.GitRefused, match=r"README\.md"):
        await git.switch(repo, "other", None)
    assert await git.current_branch(repo) == "main"
    assert (repo / "README.md").read_text(encoding="utf-8") == "edited\n"


async def test_untracked_files_block_only_when_they_would_be_overwritten(
    remote: str, tmp_path: Path
) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    _remote_branch(remote, tmp_path, "feature", "f.md")
    (repo / "notes.txt").write_text("mine\n", encoding="utf-8")  # the branch lacks it
    (repo / "f.md").write_text("my own f\n", encoding="utf-8")  # the branch has one
    with pytest.raises(git.GitRefused, match=r"f\.md"):
        await git.switch(repo, "feature", None)
    assert (repo / "f.md").read_text(encoding="utf-8") == "my own f\n"
    (repo / "f.md").unlink()
    await git.switch(repo, "feature", None)
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "mine\n"  # carried along


async def test_switching_is_refused_while_merging_or_detached(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    run(repo, "branch", "other")
    run(repo, "checkout", "--detach")
    with pytest.raises(git.GitRefused, match="detached"):
        await git.switch(repo, "other", None)
    run(repo, "switch", "main")
    _commit_and_push(_other_clone(remote, tmp_path), "README.md", "# Notes\n\ntheirs\n", "Theirs")
    (repo / "README.md").write_text("# Notes\n\nours\n", encoding="utf-8")
    run(repo, "commit", "-am", "Ours")
    await git.fetch(repo, "main", None)
    subprocess.run(["git", "merge", "origin/main"], cwd=repo, capture_output=True)
    with pytest.raises(git.GitRefused, match="merge"):
        await git.switch(repo, "other", None)
    with pytest.raises(git.GitRefused, match="merge"):
        await git.create_branch(repo, "new", None)


async def test_create_then_publish_even_with_autosetupmerge(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    run(repo, "config", "branch.autoSetupMerge", "always")
    (repo / "wip.md").write_text("wip\n", encoding="utf-8")  # comes along
    await git.create_branch(repo, "draft/one", None)
    st = await git.status(repo)
    assert (st.branch, st.upstream) == ("draft/one", None)
    assert run(repo, "config", "--get-all", "branch.draft/one.merge", check=False) == ""
    assert [f.path for f in st.files] == ["wip.md"]
    await git.commit_all(repo, "WIP")
    ok, detail = await git.push(repo, "draft/one", None)
    assert ok and "Published" in detail
    assert (await git.status(repo)).upstream == "origin/draft/one"


async def test_create_refuses_a_taken_name(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    run(repo, "branch", "local-only")
    _remote_branch(remote, tmp_path, "theirs", "t.md")
    with pytest.raises(git.GitRefused, match="already exists"):
        await git.create_branch(repo, "local-only", None)
    with pytest.raises(git.GitRefused, match="remote already has"):
        await git.create_branch(repo, "theirs", None)
    for bad in ("-x", "a..b", "HEAD", "a b"):
        with pytest.raises(git.GitInputError):
            await git.create_branch(repo, bad, None)
    assert await git.current_branch(repo) == "main"


async def test_switching_to_an_unknown_branch(remote: str, tmp_path: Path) -> None:
    repo = await _clone(remote, tmp_path / "ws")
    with pytest.raises(git.GitNotFound):
        await git.switch(repo, "nowhere", None)


# --- API ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from precursor.backend.main import create_app
    from precursor.backend.routers import workspaces as router

    async def no_token(_session: Any) -> None:
        return None

    monkeypatch.setattr(router, "resolve_github_token", no_token)
    with TestClient(create_app()) as test_client:
        yield test_client


def _workspace(client: TestClient, remote: str, name: str) -> dict[str, Any]:
    resp = client.post(
        "/api/workspaces",
        json={"name": name, "kind": "git", "repo_url": remote, "branch": "main"},
    )
    assert resp.status_code == 201, resp.text
    ws: dict[str, Any] = resp.json()
    return ws


def test_api_commit_push_fetch_round_trip(client: TestClient, remote: str, tmp_path: Path) -> None:
    ws = _workspace(client, remote, "Git round trip")
    base = f"/api/workspaces/{ws['id']}"
    try:
        client.put(f"{base}/file", params={"path": "a.md"}, json={"content": "a\n"})
        resp = client.post(f"{base}/git/commit", json={"message": "Add a", "paths": ["a.md"]})
        assert resp.json()["ok"] and resp.json()["status"]["ahead"] == 1

        resp = client.post(f"{base}/git/push")
        assert resp.json()["ok"] and resp.json()["status"]["ahead"] == 0

        _commit_and_push(_other_clone(remote, tmp_path), "b.md", "b\n", "Theirs")
        resp = client.post(f"{base}/git/fetch")
        assert resp.json()["ok"] and resp.json()["status"]["behind"] == 1

        resp = client.post(f"{base}/git/pull")
        assert resp.json()["ok"] and resp.json()["status"]["behind"] == 0
    finally:
        client.delete(base)


def test_api_rejects_unsafe_paths(client: TestClient, remote: str) -> None:
    ws = _workspace(client, remote, "Git paths")
    base = f"/api/workspaces/{ws['id']}"
    try:
        for path in ("../x", ".git/config", "/etc/passwd"):
            assert client.get(f"{base}/git/diff", params={"path": path}).status_code == 400
            assert client.post(f"{base}/git/discard", params={"path": path}).status_code == 400
        resp = client.post(f"{base}/git/commit", json={"message": "m", "paths": ["../x"]})
        assert resp.status_code == 400
        for params in ({"path": "../x"}, {"path": "README.md", "base": "HEAD^"}):
            resp = client.get(f"{base}/git/file-versions", params=params)
            assert resp.status_code == 400
        ok = client.get(f"{base}/git/file-versions", params={"path": "README.md"}).json()
        assert ok["original"] == ok["modified"] == "# Notes\n\nfirst line\n"
        for params in ({"limit": 0}, {"limit": 101}, {"skip": -1}, {"path": "../x"}):
            assert client.get(f"{base}/git/log", params=params).status_code in (400, 422)
        assert client.get(f"{base}/git/commits/HEAD~1").status_code == 400
        assert client.get(f"{base}/git/commits/deadbeef").status_code == 404
        [root] = client.get(f"{base}/git/log").json()["commits"]
        detail = client.get(f"{base}/git/commits/{root['sha']}").json()
        assert detail["parent"] is None and detail["files"][0]["status"] == "A"
        added = client.get(
            f"{base}/git/file-versions", params={"path": "README.md", "head": root["sha"]}
        ).json()
        assert added["original"] is None and added["modified"] == "# Notes\n\nfirst line\n"
    finally:
        client.delete(base)


def test_api_follows_a_branch_switched_outside(client: TestClient, remote: str) -> None:
    """Checked out in a terminal: pushes go to that branch, and the row follows."""
    from precursor.backend.config import get_settings

    ws = _workspace(client, remote, "Git switched")
    base = f"/api/workspaces/{ws['id']}"
    repo = Path(get_settings().workspaces_dir) / ws["slug"]
    try:
        run(repo, "switch", "-c", "elsewhere")
        resp = client.post(f"{base}/git/pull")
        assert not resp.json()["ok"] and "isn't on the remote" in resp.json()["detail"]
        resp = client.post(f"{base}/git/push")
        assert resp.json()["ok"] and resp.json()["status"]["upstream"] == "origin/elsewhere"
        [row] = [w for w in client.get("/api/workspaces").json() if w["id"] == ws["id"]]
        assert row["branch"] == "elsewhere"
    finally:
        client.delete(base)


def test_api_switch_and_create_branches(
    client: TestClient, remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.config import get_settings
    from precursor.backend.routers import workspaces as router

    refreshed: list[bool] = []

    async def record(_session: Any) -> None:
        refreshed.append(True)

    monkeypatch.setattr(router, "_holds_definitions", lambda _root: True)
    monkeypatch.setattr(router.definition_anchors, "refresh", record)
    _remote_branch(remote, tmp_path, "feature", "f.md")
    ws = _workspace(client, remote, "Git branches")
    base = f"/api/workspaces/{ws['id']}"
    repo = Path(get_settings().workspaces_dir) / ws["slug"]

    def row_branch() -> str:
        [row] = [w for w in client.get("/api/workspaces").json() if w["id"] == ws["id"]]
        return str(row["branch"])

    try:
        listed = client.get(f"{base}/git/branches").json()
        assert listed["current"] == "main" and "feature" in listed["remote"]

        (repo / "README.md").write_text("dirty\n", encoding="utf-8")
        resp = client.post(f"{base}/git/switch", json={"name": "feature"})
        assert resp.status_code == 409 and "README.md" in resp.json()["detail"]
        assert refreshed == []
        run(repo, "checkout", "--", "README.md")

        resp = client.post(f"{base}/git/switch", json={"name": "feature"})
        assert resp.status_code == 200 and resp.json()["status"]["branch"] == "feature"
        assert row_branch() == "feature" and refreshed == [True]

        assert client.post(f"{base}/git/switch", json={"name": "nope"}).status_code == 404
        assert client.post(f"{base}/git/switch", json={"name": "a..b"}).status_code == 400

        resp = client.post(f"{base}/git/branches", json={"name": "draft"})
        assert resp.status_code == 201
        assert resp.json()["status"]["upstream"] is None and row_branch() == "draft"
        assert client.post(f"{base}/git/branches", json={"name": "main"}).status_code == 409
        resp = client.post(f"{base}/git/push")
        assert resp.json()["ok"] and resp.json()["status"]["upstream"] == "origin/draft"
    finally:
        client.delete(base)


def test_api_discard_refreshes_definitions_it_holds(
    client: TestClient, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.routers import workspaces as router

    refreshed: list[bool] = []

    async def record(_session: Any) -> None:
        refreshed.append(True)

    monkeypatch.setattr(router, "_holds_definitions", lambda _root: True)
    monkeypatch.setattr(router.definition_anchors, "refresh", record)
    ws = _workspace(client, remote, "Git defs")
    base = f"/api/workspaces/{ws['id']}"
    try:
        client.put(f"{base}/file", params={"path": "README.md"}, json={"content": "x\n"})
        assert client.post(f"{base}/git/discard", params={"path": "README.md"}).status_code == 200
        assert refreshed == [True]
    finally:
        client.delete(base)
