"""Tests for the Agent 365 hosted MCP servers (``workiq-teams`` / ``workiq-user``)."""

from __future__ import annotations

import base64
import json
from typing import ClassVar

import pytest

from precursor.backend.services.mcp import agent365, workiq_preview

TENANT = "72f988bf-86f1-41af-91ab-2d7cd011db47"


def _access_token(claims: dict[str, object]) -> str:
    """A JWT-shaped token whose payload carries ``claims`` (signature ignored)."""
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


class _Tokens:
    def __init__(self, access_token: str) -> None:
        self.access_token = access_token


class _FakeStorage:
    """Stands in for ``DbTokenStorage``, keyed by the profile's tokens key."""

    tokens_by_key: ClassVar[dict[str, str]] = {}

    def __init__(self, profile: object | None = None) -> None:
        self.key = getattr(profile, "tokens_key", "workiq_oauth_tokens")

    async def get_tokens(self) -> _Tokens | None:
        token = self.tokens_by_key.get(self.key)
        return _Tokens(token) if token else None


@pytest.fixture(autouse=True)
def _isolate_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeStorage.tokens_by_key = {}
    monkeypatch.setattr(agent365, "DbTokenStorage", _FakeStorage)


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "common", "organizations", "not-a-guid", TENANT[:-1]],
)
def test_valid_tenant_rejects_non_guids(value: str | None) -> None:
    assert agent365._valid_tenant(value) == ""


def test_valid_tenant_accepts_a_guid_and_trims() -> None:
    assert agent365._valid_tenant(f"  {TENANT.upper()} ") == TENANT.upper()


def test_is_agent365_server() -> None:
    assert agent365.is_agent365_server("workiq-teams")
    assert agent365.is_agent365_server("workiq-user")
    assert not agent365.is_agent365_server("workiq")


def test_server_url_embeds_the_tenant() -> None:
    assert agent365.server_url("workiq-teams", TENANT) == (
        f"https://agent365.svc.cloud.microsoft/agents/tenants/{TENANT}/servers/mcp_TeamsServer"
    )
    assert agent365.server_url("workiq-user", TENANT).endswith("/servers/mcp_MeServer")


def test_build_profile_keeps_each_server_isolated() -> None:
    teams = agent365.build_profile("workiq-teams", TENANT)
    user = agent365.build_profile("workiq-user", TENANT)

    assert teams.client_id == user.client_id == agent365.AGENT365_CLIENT_ID
    # Distinct loopback ports and AppSetting keys so two sign-ins never collide.
    assert teams.redirect_port != user.redirect_port
    assert teams.redirect_port not in (12798,)  # reserved for the WorkIQ preview
    assert teams.tokens_key == "workiq_teams_oauth_tokens"
    assert teams.issued_at_key == "workiq_teams_oauth_issued_at"
    assert user.tokens_key == "workiq_user_oauth_tokens"
    # Entra ignores the *port* of a public client's loopback redirect but matches
    # host and path exactly — the Agent 365 client registered a bare ``localhost``
    # root, so ``127.0.0.1`` or a ``/callback`` path is rejected (AADSTS50011).
    assert teams.redirect_uri == f"http://localhost:{teams.redirect_port}/"


def test_build_profile_shares_the_login_hint_with_the_workiq_family() -> None:
    # Same Microsoft identity everywhere: sharing the hint lets a server that has
    # never signed in reuse the known account instead of tripping AADSTS16000.
    teams = agent365.build_profile("workiq-teams", TENANT)
    user = agent365.build_profile("workiq-user", TENANT)

    assert teams.login_hint_key == user.login_hint_key == workiq_preview.OAUTH_LOGIN_HINT_KEY


async def test_discover_tenant_id_reads_the_tid_claim() -> None:
    _FakeStorage.tokens_by_key["workiq_oauth_tokens"] = _access_token({"tid": TENANT})
    assert await agent365.discover_tenant_id() == TENANT


async def test_discover_tenant_id_falls_back_to_an_agent365_token() -> None:
    _FakeStorage.tokens_by_key["workiq_teams_oauth_tokens"] = _access_token({"tid": TENANT})
    assert await agent365.discover_tenant_id() == TENANT


async def test_discover_tenant_id_ignores_a_token_without_a_tenant() -> None:
    _FakeStorage.tokens_by_key["workiq_oauth_tokens"] = _access_token({"upn": "a@b.c"})
    assert await agent365.discover_tenant_id() == ""


async def test_discover_tenant_id_without_any_token() -> None:
    assert await agent365.discover_tenant_id() == ""


async def test_resolve_tenant_id_prefers_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    other = "11111111-2222-3333-4444-555555555555"
    _FakeStorage.tokens_by_key["workiq_oauth_tokens"] = _access_token({"tid": other})

    async def _configured(_session: object) -> str:
        return TENANT

    monkeypatch.setattr(agent365, "resolve_workiq_tenant_id", _configured)
    assert await agent365.resolve_tenant_id() == TENANT


async def test_resolve_tenant_id_ignores_a_malformed_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeStorage.tokens_by_key["workiq_oauth_tokens"] = _access_token({"tid": TENANT})

    async def _configured(_session: object) -> str:
        return "organizations"

    monkeypatch.setattr(agent365, "resolve_workiq_tenant_id", _configured)
    assert await agent365.resolve_tenant_id() == TENANT


async def test_profile_for_requires_a_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _none() -> str:
        return ""

    monkeypatch.setattr(agent365, "resolve_tenant_id", _none)
    assert await agent365.profile_for("workiq-teams") is None


async def test_profile_for_unknown_server(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _tenant() -> str:
        return TENANT

    monkeypatch.setattr(agent365, "resolve_tenant_id", _tenant)
    assert await agent365.profile_for("workiq") is None


async def test_profile_for_returns_a_live_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _tenant() -> str:
        return TENANT

    monkeypatch.setattr(agent365, "resolve_tenant_id", _tenant)
    profile = await agent365.profile_for("workiq-user")
    assert profile is not None
    assert profile.url == agent365.server_url("workiq-user", TENANT)


async def test_configure_agent365_servers_without_a_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []

    class _Manager:
        def configure_agent365(self, name: str, *, url: str | None, auth_provider: object) -> None:
            calls.append((name, url))

    monkeypatch.setattr(
        "precursor.backend.services.mcp.client.get_mcp_client_manager",
        lambda: _Manager(),
    )

    async def _none() -> str:
        return ""

    monkeypatch.setattr(agent365, "resolve_tenant_id", _none)
    await agent365.configure_agent365_servers()
    assert calls == [("workiq-teams", None), ("workiq-user", None)]


async def test_configure_agent365_servers_points_at_the_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []

    class _Manager:
        def configure_agent365(self, name: str, *, url: str | None, auth_provider: object) -> None:
            calls.append((name, url))

    monkeypatch.setattr(
        "precursor.backend.services.mcp.client.get_mcp_client_manager",
        lambda: _Manager(),
    )
    monkeypatch.setattr(agent365, "build_oauth_provider", lambda **_kwargs: object())

    async def _tenant() -> str:
        return TENANT

    monkeypatch.setattr(agent365, "resolve_tenant_id", _tenant)
    await agent365.configure_agent365_servers()
    assert calls == [
        ("workiq-teams", agent365.server_url("workiq-teams", TENANT)),
        ("workiq-user", agent365.server_url("workiq-user", TENANT)),
    ]
