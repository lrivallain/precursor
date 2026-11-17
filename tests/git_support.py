"""Git helpers for the workspace-git tests: isolated config and a seeded remote."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def run(cwd: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=check, capture_output=True, text=True
    ).stdout


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def isolated_git_env(config: Path) -> dict[str, str]:
    """An empty global config and a fixed identity, so no developer setting leaks in."""
    config.write_text("", encoding="utf-8", newline="\n")
    env = {"GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1"}
    for who in ("AUTHOR", "COMMITTER"):
        env[f"GIT_{who}_NAME"] = "Test"
        env[f"GIT_{who}_EMAIL"] = "test@example.com"
    return env


def seed_bare_remote(root: Path) -> Path:
    """A bare remote under ``root`` whose ``main`` has one commit (README.md).

    Built once per worker and copied per test: seven git processes per test
    added up, process start-up being what git costs most on Windows. A bare
    repository keeps no record of its own path, so a copy is as good as new.
    """
    with pytest.MonkeyPatch.context() as patch:
        for name, value in isolated_git_env(root / "gitconfig").items():
            patch.setenv(name, value)
        bare = root / "remote.git"
        run(root, "init", "--bare", "--initial-branch=main", str(bare))
        seed = root / "seed"
        run(root, "init", "--initial-branch=main", str(seed))
        write_text(seed / "README.md", "# Notes\n\nfirst line\n")
        run(seed, "add", "-A")
        run(seed, "commit", "-m", "Initial")
        run(seed, "remote", "add", "origin", bare.as_uri())
        run(seed, "push", "origin", "main")
    return bare
