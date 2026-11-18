"""Pinning which ``gh`` account supplies the token.

``gh auth token`` follows the CLI's *active* account, so with several logins the
effective token depends on whoever last ran ``gh auth switch`` — which makes a
launcher-started instance's credentials depend on unrelated shell state.
``PRECURSOR_GITHUB_CLI_USER`` removes that coupling.
Settings → GitHub can also pick the login; that pick wins over the env var.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
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


class _FakeSession:
    """Just enough of an AsyncSession for the AppSetting lookups."""

    def __init__(self, **values: Any) -> None:
        self._values = values

    async def get(self, _model: Any, key: str) -> Any:
        if key not in self._values:
            return None
        return SimpleNamespace(value=json.dumps(self._values[key]))


_STATUS = {
    "hosts": {
        "github.com": [
            {"state": "success", "active": True, "login": "personal"},
            {"state": "success", "active": False, "login": "work"},
            {"state": "error", "active": False, "login": "expired"},
        ]
    }
}


def test_accounts_are_listed_from_gh_auth_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert cmd[:4] == ["gh", "auth", "status", "--json"]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_STATUS), stderr="")

    monkeypatch.setattr(github_auth.subprocess, "run", fake_run)
    assert github_auth.list_gh_accounts() == [
        github_auth.GhAccount(login="personal", host="github.com", active=True),
        github_auth.GhAccount(login="work", host="github.com", active=False),
    ]


def test_old_gh_without_json_lists_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _record_argv(monkeypatch, returncode=1)
    assert github_auth.list_gh_accounts() == []


def _token_per_user(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        user = cmd[cmd.index("--user") + 1] if "--user" in cmd else "active"
        return subprocess.CompletedProcess(cmd, 0, stdout=f"tok-{user}\n", stderr="")

    monkeypatch.setattr(github_auth.subprocess, "run", fake_run)
    return seen


async def test_account_picked_in_settings_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRECURSOR_GITHUB_CLI_USER", "from-env")
    config.get_settings.cache_clear()
    _token_per_user(monkeypatch)
    session: Any = _FakeSession(github_cli_account="work")
    assert await github_auth.resolve_github_token(session) == "tok-work"
    assert await github_auth.github_token_source(session) == "gh-cli"


async def test_no_pick_falls_back_to_env_then_active(monkeypatch: pytest.MonkeyPatch) -> None:
    _token_per_user(monkeypatch)
    unpicked: Any = _FakeSession()
    assert await github_auth.resolve_github_token(unpicked) == "tok-active"
    monkeypatch.setenv("PRECURSOR_GITHUB_CLI_USER", "from-env")
    config.get_settings.cache_clear()
    assert await github_auth.resolve_github_token(unpicked) == "tok-from-env"


async def test_switching_account_does_not_reuse_the_cached_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _token_per_user(monkeypatch)
    personal: Any = _FakeSession(github_cli_account="personal")
    work: Any = _FakeSession(github_cli_account="work")
    assert await github_auth.resolve_github_token(personal) == "tok-personal"
    assert await github_auth.resolve_github_token(work) == "tok-work"


async def test_saved_pat_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _token_per_user(monkeypatch)
    session: Any = _FakeSession(github_cli_account="work", api_keys={"github_token": "pat"})
    assert await github_auth.resolve_github_token(session) == "pat"
    assert seen == []


def test_accounts_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from precursor.backend.main import create_app
    from precursor.backend.routers import me as me_router

    monkeypatch.setattr(
        me_router,
        "list_gh_accounts",
        lambda: [github_auth.GhAccount(login="work", host="github.com", active=True)],
    )
    with TestClient(create_app()) as client:
        body = client.get("/api/me/github-accounts").json()
    assert body["accounts"] == [{"login": "work", "active": True}]
    assert body["selected"] == ""
