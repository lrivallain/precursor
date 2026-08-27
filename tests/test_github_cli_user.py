"""Pinning which ``gh`` account supplies the token.

``gh auth token`` follows the CLI's *active* account, so with several logins the
effective token depends on whoever last ran ``gh auth switch`` — which makes a
launcher-started instance's credentials depend on unrelated shell state.
``PRECURSOR_GITHUB_CLI_USER`` removes that coupling.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from precursor.backend import config
from precursor.backend.services import github_auth


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    github_auth.invalidate_gh_cli_token()
    config.get_settings.cache_clear()
    monkeypatch.setattr(github_auth.shutil, "which", lambda _name: "/usr/bin/gh")
    yield
    github_auth.invalidate_gh_cli_token()
    config.get_settings.cache_clear()


def _record_argv(monkeypatch: pytest.MonkeyPatch, *, returncode: int = 0) -> list[list[str]]:
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout="ghp_token\n", stderr="")

    monkeypatch.setattr(github_auth.subprocess, "run", fake_run)
    return seen


def test_no_user_configured_keeps_the_bare_command(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _record_argv(monkeypatch)
    assert github_auth._run_gh_auth_token() == "ghp_token"
    assert seen == [["gh", "auth", "token"]]


def test_configured_user_is_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRECURSOR_GITHUB_CLI_USER", "someone_microsoft")
    config.get_settings.cache_clear()
    seen = _record_argv(monkeypatch)
    assert github_auth._run_gh_auth_token() == "ghp_token"
    assert seen == [["gh", "auth", "token", "--user", "someone_microsoft"]]


def test_blank_user_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRECURSOR_GITHUB_CLI_USER", "   ")
    config.get_settings.cache_clear()
    seen = _record_argv(monkeypatch)
    github_auth._run_gh_auth_token()
    assert seen == [["gh", "auth", "token"]]


def test_unknown_user_yields_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRECURSOR_GITHUB_CLI_USER", "not-signed-in")
    config.get_settings.cache_clear()
    _record_argv(monkeypatch, returncode=1)
    # Degrades to "no token" (mock provider) rather than raising.
    assert github_auth._run_gh_auth_token() == ""


def test_missing_gh_is_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_auth.shutil, "which", lambda _name: None)
    assert github_auth._run_gh_auth_token() == ""
