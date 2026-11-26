"""Remote access to the UI through a tunnel (issue #521).

A request carrying forwarding headers came through a tunnel or proxy. Once
remote access is on, it needs a session from the QR pairing; the local-only
features never answer it. Local requests must behave exactly as before.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services.links import app_base_url
from precursor.backend.services.remote_access import classify
from precursor.backend.services.remote_access import sessions as store
from precursor.backend.services.remote_access.gate import COOKIE_NAME
from precursor.backend.services.remote_access.pairing import (
    MAX_FAILED_ATTEMPTS,
    PairingCodes,
    pairing_codes,
)
from tests.support import set_app_setting

TUNNEL = {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Host": "abc-8000.euw.devtunnels.ms"}


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    asyncio.run(set_app_setting(store.SETTING_KEY, {"enabled": False, "sessions": []}))
    pairing_codes.cancel()
    yield
    asyncio.run(set_app_setting(store.SETTING_KEY, {"enabled": False, "sessions": []}))
    pairing_codes.cancel()


def _client() -> TestClient:
    # https: the session cookie is Secure, as it is through the tunnel.
    return TestClient(create_app(), base_url="https://testserver")


def _pair(client: TestClient) -> None:
    """Turn remote access on (locally) and pair this client through the tunnel."""
    assert client.put("/api/remote/access", json={"enabled": True}).status_code == 200
    path = client.post("/api/remote/pairing").json()["path"]
    r = client.get(path, headers=TUNNEL, follow_redirects=False)
    assert r.status_code == 303
    assert COOKIE_NAME in r.cookies


# --- classification ---------------------------------------------------------


@pytest.mark.parametrize(
    ("headers", "client", "remote"),
    [
        ([], ("127.0.0.1", 5000), False),
        ([], ("::1", 5000), False),
        ([], ("testclient", 50000), False),  # not a network peer
        ([], None, False),
        ([], ("192.168.1.20", 5000), True),
        ([(b"x-forwarded-for", b"1.2.3.4")], ("127.0.0.1", 5000), True),
        ([(b"X-Forwarded-Host", b"t.devtunnels.ms")], ("127.0.0.1", 5000), True),
        ([(b"x-forwarded-proto", b"https")], ("127.0.0.1", 5000), True),
        ([(b"forwarded", b"for=1.2.3.4")], ("127.0.0.1", 5000), True),
    ],
)
def test_is_remote(headers: list, client: object, remote: bool) -> None:
    assert classify.is_remote({"headers": headers, "client": client}) is remote


# --- pairing codes and sessions ---------------------------------------------


def test_pairing_code_is_single_use() -> None:
    codes = PairingCodes()
    code, _ = codes.mint()
    assert codes.consume(code) is True
    assert codes.consume(code) is False


def test_minting_replaces_the_previous_code() -> None:
    codes = PairingCodes()
    first, _ = codes.mint()
    second, _ = codes.mint()
    assert codes.consume(first) is False
    assert codes.consume(second) is True


def test_pairing_code_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    from precursor.backend.services.remote_access import pairing

    codes = PairingCodes()
    code, expires_at = codes.mint()
    monkeypatch.setattr(pairing, "utcnow", lambda: expires_at + timedelta(seconds=1))
    assert codes.pending_until() is None
    assert codes.consume(code) is False


def test_wrong_guesses_burn_the_code() -> None:
    codes = PairingCodes()
    code, _ = codes.mint()
    for _ in range(MAX_FAILED_ATTEMPTS):
        assert codes.consume("wrong") is False
    assert codes.consume(code) is False


def test_session_tokens_are_stored_hashed_and_expire(monkeypatch: pytest.MonkeyPatch) -> None:
    state = store.RemoteAccessState(enabled=True)
    token = store.issue(state, "iPhone")
    assert token not in repr(state)
    entry = store.find(state, token)
    assert entry is not None and entry.label == "iPhone"
    assert store.find(state, "other") is None
    later = store.utcnow() + store.SESSION_TTL + timedelta(minutes=1)
    monkeypatch.setattr(store, "utcnow", lambda: later)
    assert store.find(state, token) is None


def test_sessions_are_capped() -> None:
    state = store.RemoteAccessState(enabled=True)
    tokens = [store.issue(state, f"d{i}") for i in range(store.MAX_SESSIONS + 1)]
    assert len(state.sessions) == store.MAX_SESSIONS
    assert store.find(state, tokens[0]) is None
    assert store.find(state, tokens[-1]) is not None


# --- the gate ---------------------------------------------------------------


def test_remote_access_off_changes_nothing() -> None:
    # e.g. behind the user's own authenticating reverse proxy.
    with _client() as client:
        assert client.get("/api/topics", headers=TUNNEL).status_code == 200


def test_unpaired_remote_requests_are_refused() -> None:
    with _client() as client:
        client.put("/api/remote/access", json={"enabled": True})
        api = client.get("/api/topics", headers=TUNNEL)
        assert api.status_code == 401
        assert api.json() == {"detail": "This device is not paired."}
        page = client.get("/", headers=TUNNEL)
        assert page.status_code == 401
        assert "Pair this device" in page.text
        # What the home-screen install needs stays reachable.
        assert client.get("/manifest.webmanifest", headers=TUNNEL).status_code != 401
        # Local requests never need a session.
        assert client.get("/api/topics").status_code == 200


def test_paired_device_reaches_the_app() -> None:
    with _client() as client:
        _pair(client)
        assert client.get("/api/topics", headers=TUNNEL).status_code == 200
        assert client.get("/api/remote/context", headers=TUNNEL).json() == {"remote": True}
        assert client.get("/api/remote/context").json() == {"remote": False}


def test_a_bad_or_used_code_does_not_pair() -> None:
    with _client() as client:
        client.put("/api/remote/access", json={"enabled": True})
        path = client.post("/api/remote/pairing").json()["path"]
        assert client.get(path, headers=TUNNEL, follow_redirects=False).status_code == 303
        client.cookies.clear()
        reused = client.get(path, headers=TUNNEL, follow_redirects=False)
        assert reused.status_code == 401
        assert COOKIE_NAME not in reused.cookies
        assert client.get("/api/topics", headers=TUNNEL).status_code == 401


def test_the_pairing_link_is_refused_on_the_pc_itself() -> None:
    with _client() as client:
        client.put("/api/remote/access", json={"enabled": True})
        path = client.post("/api/remote/pairing").json()["path"]
        code = parse_qs(urlsplit(path).query)["code"][0]
        assert client.get(path, follow_redirects=False).status_code == 404
        # ...and that didn't burn the code.
        assert pairing_codes.consume(code) is True


def test_revoking_a_session_locks_the_device_out() -> None:
    with _client() as client:
        _pair(client)
        sessions = client.get("/api/remote/access").json()["sessions"]
        assert len(sessions) == 1
        assert client.delete(f"/api/remote/sessions/{sessions[0]['id']}").status_code == 204
        assert client.get("/api/topics", headers=TUNNEL).status_code == 401


def test_revoking_all_sessions() -> None:
    with _client() as client:
        _pair(client)
        assert client.delete("/api/remote/sessions").status_code == 204
        assert client.get("/api/remote/access").json()["sessions"] == []


def test_minting_needs_remote_access_on() -> None:
    with _client() as client:
        assert client.post("/api/remote/pairing").status_code == 409


# --- local-only features ----------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/remote/access"),
        ("PUT", "/api/remote/access"),
        ("POST", "/api/remote/pairing"),
        ("DELETE", "/api/remote/sessions"),
    ],
)
def test_remote_control_is_local_only_even_when_paired(method: str, path: str) -> None:
    with _client() as client:
        _pair(client)
        r = client.request(method, path, headers=TUNNEL, json={"enabled": False})
        assert r.status_code == 404


def test_mcp_http_is_local_only() -> None:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    with TestClient(create_app(), base_url=app_base_url()) as client:
        client.put("/api/settings", json={"mcp_http_enabled": True})
        try:
            assert client.post("/mcp", json=init, headers=headers).status_code == 200
            # A tunnel forwards to loopback and may rewrite Host to localhost.
            remote = client.post("/mcp", json=init, headers={**headers, **TUNNEL})
            assert remote.status_code == 404
        finally:
            client.put("/api/settings", json={"mcp_http_enabled": False})


def test_plugin_installer_refuses_tunneled_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    from precursor.backend.plugins import install as install_mod

    monkeypatch.setattr(install_mod, "_environment_locked_while_running", lambda: False)
    local = {"Host": "localhost:8000"}
    with TestClient(create_app()) as client:
        assert client.get("/api/plugins/environment", headers=local).json()["installable_here"]
        tunneled = {**local, **TUNNEL}
        env = client.get("/api/plugins/environment", headers=tunneled).json()
        assert env["installable_here"] is False
        client.put("/api/settings", json={"plugin_install_enabled": True})
        try:
            r = client.post("/api/plugins/install", json={"package": "x"}, headers=tunneled)
            assert r.status_code == 403
            assert "localhost" in r.json()["detail"]
        finally:
            client.put("/api/settings", json={"plugin_install_enabled": False})
