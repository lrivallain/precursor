"""Remote access to the UI through a tunnel (issue #521).

A request carrying forwarding headers came through a tunnel or proxy. Once
remote access is on, it needs a session from the QR pairing; the local-only
features never answer it. Local requests must behave exactly as before.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
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

TUNNEL = {
    "X-Forwarded-For": "203.0.113.7",
    "X-Forwarded-Host": "abc-8000.euw.devtunnels.ms",
    # What the app's own pages send on every call (see the forgery tests).
    "X-Client-Id": "test-window",
}


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    asyncio.run(set_app_setting(store.SETTING_KEY, {"enabled": False, "sessions": []}))
    pairing_codes.cancel()
    yield
    asyncio.run(set_app_setting(store.SETTING_KEY, {"enabled": False, "sessions": []}))
    pairing_codes.cancel()


def _client() -> TestClient:
    # https: the session cookie is Secure, as it is through the tunnel. And
    # localhost: local-only routes want a loopback Host (dev tunnels rewrite
    # Host to localhost too).
    return TestClient(create_app(), base_url="https://localhost")


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
        assert client.get("/api/remote/context", headers=TUNNEL).json()["remote"] is True
        local = client.get("/api/remote/context").json()
        assert local["remote"] is False
        assert local["app_origin"] == app_base_url()


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


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "attacker.example"},  # a DNS-rebinding page
        {"Origin": "https://attacker.example"},  # a cross-site form or fetch
    ],
)
def test_local_only_routes_refuse_foreign_host_or_origin(headers: dict[str, str]) -> None:
    with _client() as client:
        assert client.get("/api/remote/access", headers=headers).status_code == 404
        assert (
            client.put("/api/remote/access", json={"enabled": True}, headers=headers).status_code
            == 404
        )
        assert client.get("/api/remote/access").json()["enabled"] is False


def _age_sessions(hours: int) -> None:
    """Make every stored session look last used ``hours`` ago."""

    async def run() -> None:
        def age(state: store.RemoteAccessState) -> None:
            past = (store.utcnow() - timedelta(hours=hours)).isoformat()
            for entry in state.sessions:
                entry.last_used_at = past

        await store.update(age)

    asyncio.run(run())


def test_using_a_session_renews_its_cookie() -> None:
    with _client() as client:
        _pair(client)
        # Fresh: no renewal on every request.
        assert "set-cookie" not in client.get("/api/topics", headers=TUNNEL).headers
        _age_sessions(2)
        r = client.get("/api/topics", headers=TUNNEL)
        assert r.status_code == 200
        cookie = r.headers["set-cookie"]
        assert cookie.startswith(f"{COOKIE_NAME}=")
        for attribute in ("Max-Age=2592000", "Secure", "HttpOnly", "SameSite=Lax", "Path=/"):
            assert attribute in cookie
        before = store.utcnow() - timedelta(minutes=5)
        sessions = client.get("/api/remote/access").json()["sessions"]
        assert sessions[0]["last_used_at"] > before.isoformat()


def test_a_stale_read_cannot_bring_a_revoked_device_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate read the device, then it was revoked before the gate touched it."""
    with _client() as client:
        _pair(client)
        _age_sessions(2)

        async def snapshot() -> store.RemoteAccessState:
            from precursor.backend.db import SessionLocal

            async with SessionLocal() as db:
                return await store.load(db)

        stale = asyncio.run(snapshot())
        assert client.delete("/api/remote/sessions").status_code == 204
        real_load = store.load
        calls = 0

        async def load_stale_first(db: Any) -> store.RemoteAccessState:
            nonlocal calls
            calls += 1
            return stale if calls == 1 else await real_load(db)

        monkeypatch.setattr(store, "load", load_stale_first)
        assert client.get("/api/topics", headers=TUNNEL).status_code == 401
        monkeypatch.setattr(store, "load", real_load)
        assert client.get("/api/remote/access").json()["sessions"] == []


def test_openai_endpoint_keeps_its_own_auth_when_remote_access_is_on() -> None:
    with _client() as client:
        client.put("/api/remote/access", json={"enabled": True})
        r = client.get("/api/openai/v1/models", headers={**TUNNEL, "Authorization": "Bearer x"})
        # The endpoint's own answer (off by default), not the pairing 401.
        assert r.status_code == 404
        assert "pair" not in r.text.lower()


async def _paired_token() -> str:
    def setup(state: store.RemoteAccessState) -> str:
        state.enabled = True
        return store.issue(state, "phone")

    _state, token = await store.update(setup)
    return token


def _remote_scope(token: str | None) -> dict[str, Any]:
    headers = [(b"host", b"localhost"), (b"x-forwarded-for", b"203.0.113.7")]
    if token:
        headers.append((b"cookie", f"{COOKIE_NAME}={token}".encode()))
    return {"type": "http", "method": "GET", "path": "/api/events", "headers": headers}


async def _run_behind_gate(token: str | None, app: Any) -> list[bytes]:
    """Drive ``app`` behind the gate as a tunneled request; the body chunks sent."""
    from precursor.backend.services.remote_access.gate import RemoteAccessGate

    sent: list[bytes] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b""}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            sent.append(message.get("body", b""))

    await RemoteAccessGate(app)(_remote_scope(token), receive, send)
    return sent


async def _stream_through_gate(
    token: str | None, change: Any
) -> tuple[list[bytes], dict[str, Any]]:
    """Run a fake streaming app behind the gate; ``change`` runs between chunks."""
    after: dict[str, Any] = {}

    async def app(_scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"one", "more_body": True})
        await change()
        await send({"type": "http.response.body", "body": b"two", "more_body": True})
        after.update(await receive())

    return await _run_behind_gate(token, app), after


def test_revoking_a_device_ends_its_open_stream() -> None:
    async def run() -> tuple[list[bytes], dict[str, Any]]:
        token = await _paired_token()
        return await _stream_through_gate(token, lambda: store.update(store.revoke))

    sent, after = asyncio.run(run())
    assert sent == [b"one", b""]  # ended, "two" never delivered
    assert after == {"type": "http.disconnect"}


def test_an_unrelated_change_keeps_a_paired_stream_open() -> None:
    async def run() -> tuple[list[bytes], dict[str, Any]]:
        token = await _paired_token()
        return await _stream_through_gate(token, lambda: store.update(lambda _s: None))

    sent, after = asyncio.run(run())
    assert sent == [b"one", b"two"]
    assert after["type"] == "http.request"


def test_turning_enforcement_on_ends_unpaired_streams() -> None:
    async def run() -> tuple[list[bytes], dict[str, Any]]:
        return await _stream_through_gate(
            None, lambda: store.update(lambda state: setattr(state, "enabled", True))
        )

    sent, after = asyncio.run(run())
    assert sent == [b"one", b""]
    assert after == {"type": "http.disconnect"}


def test_cors_preflights_are_answered_before_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from precursor.backend.config import get_settings

    monkeypatch.setenv("PRECURSOR_CORS_ORIGINS", "https://app.example")
    get_settings.cache_clear()
    try:
        with _client() as client:
            client.put("/api/remote/access", json={"enabled": True})
            headers = {
                **TUNNEL,
                "Origin": "https://app.example",
                "Access-Control-Request-Method": "POST",
            }
            r = client.options("/api/topics", headers=headers)
            assert r.status_code == 200
            assert r.headers["access-control-allow-origin"] == "https://app.example"
            # The real request still needs a session, and its 401 carries CORS.
            denied = client.get("/api/topics", headers={**TUNNEL, "Origin": "https://app.example"})
            assert denied.status_code == 401
            assert denied.headers["access-control-allow-origin"] == "https://app.example"
    finally:
        get_settings.cache_clear()


def test_a_revocation_during_the_entry_check_is_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    with _client() as client:
        _pair(client)
        real_load = store.load
        raced = False

        async def load_then_revoke(db: Any) -> store.RemoteAccessState:
            nonlocal raced
            state = await real_load(db)
            if not raced:
                raced = True
                await store.update(store.revoke)  # commits while the gate checks
            return state

        monkeypatch.setattr(store, "load", load_then_revoke)
        assert client.get("/api/topics", headers=TUNNEL).status_code == 401


def test_a_stream_stops_when_its_session_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    from precursor.backend.services.remote_access import gate

    monkeypatch.setattr(gate, "_RECHECK_SECONDS", 0.0)

    async def expire_quietly() -> None:
        # Written directly: no access change is announced, only time passes.
        from precursor.backend.db import SessionLocal

        async with SessionLocal() as db:
            state = await store.load(db)
        old = (store.utcnow() - store.SESSION_TTL - timedelta(days=1)).isoformat()
        sessions = [{**s.__dict__, "last_used_at": old} for s in state.sessions]
        await set_app_setting(store.SETTING_KEY, {"enabled": True, "sessions": sessions})

    async def run() -> tuple[list[bytes], dict[str, Any]]:
        token = await _paired_token()
        return await _stream_through_gate(token, expire_quietly)

    sent, after = asyncio.run(run())
    assert sent == [b"one", b""]
    assert after == {"type": "http.disconnect"}


def test_a_paired_phone_cannot_read_or_change_the_openai_key() -> None:
    with _client() as client:
        _pair(client)
        client.put("/api/settings", json={"openai_proxy_enabled": True})
        try:
            assert client.get("/api/settings").json()["openai_proxy_key"]
            assert client.get("/api/settings", headers=TUNNEL).json()["openai_proxy_key"] == ""
            r = client.put("/api/settings", json={"openai_proxy_enabled": False}, headers=TUNNEL)
            assert r.status_code == 403
            assert client.post("/api/settings/openai-proxy/key", headers=TUNNEL).status_code == 403
            # Other settings still save from the phone, without the key.
            ok = client.put("/api/settings", json={"theme": "dark"}, headers=TUNNEL)
            assert ok.status_code == 200
            assert ok.json()["openai_proxy_key"] == ""
        finally:
            client.put("/api/settings", json={"openai_proxy_enabled": False, "theme": "system"})


def test_a_stream_recheck_does_not_slide_the_session() -> None:
    """Only request entry, which can still send Set-Cookie, renews a session."""

    async def run() -> tuple[str, str]:
        token = await _paired_token()

        async def age() -> None:
            def back(state: store.RemoteAccessState) -> None:
                past = (store.utcnow() - timedelta(hours=2)).isoformat()
                for entry in state.sessions:
                    entry.last_used_at = past

            await store.update(back)  # also an access change: the stream re-checks

        state, _ = await store.update(lambda _s: None)
        await _stream_through_gate(token, age)
        from precursor.backend.db import SessionLocal

        async with SessionLocal() as db:
            after = await store.load(db)
        return state.sessions[-1].last_used_at, after.sessions[-1].last_used_at

    before, after = asyncio.run(run())
    assert after < before  # still the aged value: the re-check didn't touch it


@pytest.mark.parametrize("path", ["/api/agents/hooks/nope", "/api/workflows/hooks/nope"])
def test_webhooks_need_pairing_through_the_tunnel(path: str) -> None:
    """A paired phone can read webhook tokens: they must not outlive revocation."""
    with _client() as client:
        client.put("/api/remote/access", json={"enabled": True})
        assert client.post(path, headers=TUNNEL, json={}).status_code == 401
        # Locally they behave as before.
        assert client.post(path, json={}).status_code != 401


def test_signing_out_every_device_rotates_the_openai_key() -> None:
    with _client() as client:
        _pair(client)
        client.put("/api/settings", json={"openai_proxy_enabled": True})
        try:
            before = client.get("/api/settings").json()["openai_proxy_key"]
            assert client.delete("/api/remote/sessions").status_code == 204
            after = client.get("/api/settings").json()["openai_proxy_key"]
            assert before and after and after != before
        finally:
            client.put("/api/settings", json={"openai_proxy_enabled": False})


def test_signing_out_one_device_keeps_the_openai_key() -> None:
    with _client() as client:
        _pair(client)
        client.put("/api/settings", json={"openai_proxy_enabled": True})
        try:
            before = client.get("/api/settings").json()["openai_proxy_key"]
            device = client.get("/api/remote/access").json()["sessions"][0]["id"]
            assert client.delete(f"/api/remote/sessions/{device}").status_code == 204
            assert client.get("/api/settings").json()["openai_proxy_key"] == before
        finally:
            client.put("/api/settings", json={"openai_proxy_enabled": False})


def test_a_forged_cross_site_mutation_is_refused_even_with_the_cookie() -> None:
    """Every dev tunnel shares the devtunnels.ms site, so Lax sends the cookie
    along with a form POST from someone else's tunnel. It has no app header."""
    forged = {k: v for k, v in TUNNEL.items() if k != "X-Client-Id"}
    with _client() as client:
        _pair(client)
        assert client.post("/api/topics", json={"title": "x"}, headers=forged).status_code == 401
        # Reads don't need the header, and the app's own calls go through.
        assert client.get("/api/topics", headers=forged).status_code == 200
        created = client.post("/api/topics", json={"title": "from the phone"}, headers=TUNNEL)
        assert created.status_code in (200, 201)
        # sendBeacon can't set headers: its sign-in cancel stays reachable.
        beacon = client.post("/api/mcp/servers/x/reauthenticate/cancel", headers=forged)
        assert beacon.status_code != 401
        # Locally nothing changes.
        assert client.post("/api/topics", json={"title": "local"}).status_code in (200, 201)


def test_concurrent_sends_wait_for_a_pending_recheck(monkeypatch: pytest.MonkeyPatch) -> None:
    """A keep-alive sent from another task must not slip out while a re-check runs."""
    from precursor.backend.services.remote_access import gate

    async def run() -> list[bytes]:
        token = await _paired_token()
        real_check = gate._check

        async def slow_check(scope: Any, *, touch: bool = True) -> tuple[bool, str | None]:
            await asyncio.sleep(0.05)
            return await real_check(scope, touch=touch)

        async def app(_scope: Any, _receive: Any, send: Any) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await store.update(store.revoke)
            monkeypatch.setattr(gate, "_check", slow_check)
            await asyncio.gather(
                send({"type": "http.response.body", "body": b"event", "more_body": True}),
                send({"type": "http.response.body", "body": b"ping", "more_body": True}),
            )

        return await _run_behind_gate(token, app)

    assert asyncio.run(run()) == [b""]  # ended once, nothing delivered


def test_a_paired_device_can_be_renamed_from_the_computer_only() -> None:
    with _client() as client:
        _pair(client)
        device = client.get("/api/remote/access").json()["sessions"][0]["id"]
        r = client.patch(f"/api/remote/sessions/{device}", json={"label": "  Ludo's iPhone\u0007 "})
        assert r.status_code == 200
        assert r.json()["label"] == "Ludo's iPhone"
        assert client.get("/api/remote/access").json()["sessions"][0]["label"] == "Ludo's iPhone"
        assert client.patch("/api/remote/sessions/nope", json={"label": "x"}).status_code == 404
        for blank in ("", "   ", "\u0007\u0008", " \t "):
            r = client.patch(f"/api/remote/sessions/{device}", json={"label": blank})
            assert r.status_code == 422, blank
        # Not from the phone: device management is local-only.
        tunneled = client.patch(
            f"/api/remote/sessions/{device}", json={"label": "x"}, headers=TUNNEL
        )
        assert tunneled.status_code == 404


@pytest.mark.parametrize(
    ("user_agent", "label"),
    [
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 27_2 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/27.2 Mobile/15E148 Safari/604.1",
            "iPhone · Safari",
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 27_2 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) EdgiOS/140.0 Version/27.0 Mobile/15E148 Safari/604.1",
            "iPhone · Edge",
        ),
        (
            "Mozilla/5.0 (Linux; Android 16; Pixel 10) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0 Mobile Safari/537.36 EdgA/140.0",
            "Android · Edge",
        ),
        ("Mozilla/5.0 (Linux; Android 16) Chrome/140.0 Mobile Safari/537.36", "Android · Chrome"),
        ("curl/8.7", "curl/8.7"),
    ],
)
def test_a_new_device_gets_a_readable_name(user_agent: str, label: str) -> None:
    assert store.device_label(user_agent) == label
