"""Cockpit CRUD, lifecycle, and reverse-proxy tests.

Covers the persisted definition API plus the ephemeral process runtime: the
``CockpitManager`` readiness/crash detection and the reverse proxy that strips
framing headers and rewrites root-relative asset URLs so a cockpit can be
embedded in an iframe.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import time

from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services.cockpits import CockpitManager


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


# --------------------------------------------------------------------------
# CRUD + validation (no process spawned)
# --------------------------------------------------------------------------


def test_cockpit_crud() -> None:
    app = create_app()
    with TestClient(app) as client:
        created = client.post(
            "/api/cockpits",
            json={"name": "Dashboard", "command": "echo hi", "port": 5173},
        )
        assert created.status_code == 201
        body = created.json()
        cid = body["id"]
        assert body["slug"] == "dashboard"
        assert body["status"]["state"] == "stopped"

        listed = client.get("/api/cockpits").json()
        assert [c["id"] for c in listed] == [cid]

        patched = client.patch(
            f"/api/cockpits/{cid}", json={"name": "Metrics", "port": 6006}
        ).json()
        assert patched["name"] == "Metrics"
        assert patched["port"] == 6006

        assert client.delete(f"/api/cockpits/{cid}").status_code == 204
        assert client.get("/api/cockpits").json() == []


def test_cockpit_create_rejects_bad_input() -> None:
    app = create_app()
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/cockpits",
                json={"name": "x", "command": "echo", "port": 0},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/cockpits",
                json={"name": "x", "command": "echo", "port": 99999},
            ).status_code
            == 422
        )
        # env must be a JSON object of string→string.
        assert (
            client.post(
                "/api/cockpits",
                json={"name": "x", "command": "echo", "port": 8080, "env": "not json"},
            ).status_code
            == 422
        )


def test_cockpit_slugs_are_unique() -> None:
    app = create_app()
    with TestClient(app) as client:
        a = client.post(
            "/api/cockpits", json={"name": "Tool", "command": "echo", "port": 3000}
        ).json()
        b = client.post(
            "/api/cockpits", json={"name": "Tool", "command": "echo", "port": 3001}
        ).json()
        assert a["slug"] == "tool"
        assert b["slug"] == "tool-2"


# --------------------------------------------------------------------------
# URL cockpits
# --------------------------------------------------------------------------


def test_url_cockpit_crud_and_lifecycle_guard() -> None:
    app = create_app()
    with TestClient(app) as client:
        created = client.post(
            "/api/cockpits",
            json={
                "name": "Internal Dash",
                "kind": "url",
                "url": "https://dash.internal.example.com/board",
            },
        )
        assert created.status_code == 201
        body = created.json()
        cid = body["id"]
        assert body["kind"] == "url"
        assert body["url"] == "https://dash.internal.example.com/board"
        assert body["command"] is None
        assert body["port"] is None

        # A url cockpit has no process to start.
        assert client.post(f"/api/cockpits/{cid}/start").status_code == 400
        assert client.post(f"/api/cockpits/{cid}/restart").status_code == 400

        # It can be edited (e.g. point at a new URL).
        patched = client.patch(
            f"/api/cockpits/{cid}", json={"url": "https://dash.internal.example.com/v2"}
        ).json()
        assert patched["url"] == "https://dash.internal.example.com/v2"


def test_url_cockpit_requires_valid_url() -> None:
    app = create_app()
    with TestClient(app) as client:
        # Missing url.
        assert client.post("/api/cockpits", json={"name": "x", "kind": "url"}).status_code == 422
        # Non-http(s) url.
        assert (
            client.post(
                "/api/cockpits",
                json={"name": "x", "kind": "url", "url": "ftp://nope"},
            ).status_code
            == 422
        )


def test_command_cockpit_requires_command_and_port() -> None:
    app = create_app()
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/cockpits", json={"name": "x", "kind": "command", "port": 8080}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/cockpits", json={"name": "x", "kind": "command", "command": "echo"}
            ).status_code
            == 422
        )


# --------------------------------------------------------------------------
# Lifecycle + reverse proxy (spawns a real short-lived server)
# --------------------------------------------------------------------------


def _wait_state(client: TestClient, cid: int, want: str, tries: int = 100) -> str:
    state = "stopped"
    for _ in range(tries):
        state = client.get(f"/api/cockpits/{cid}/status").json()["state"]
        if state == want:
            return state
        time.sleep(0.1)
    return state


def test_cockpit_start_proxy_stop(tmp_path) -> None:
    (tmp_path / "index.html").write_text(
        '<!doctype html><link rel="stylesheet" href="/style.css"><img src="/logo.png">',
        encoding="utf-8",
    )
    (tmp_path / "style.css").write_text("body{}", encoding="utf-8")
    port = _free_port()

    app = create_app()
    with TestClient(app) as client:
        cid = client.post(
            "/api/cockpits",
            json={
                "name": "Server",
                "command": f"{sys.executable} -m http.server {port}",
                "cwd": str(tmp_path),
                "port": port,
            },
        ).json()["id"]

        client.post(f"/api/cockpits/{cid}/start")
        assert _wait_state(client, cid, "running") == "running"

        # Proxy rewrites root-relative asset URLs and strips framing headers.
        proxied = client.get(f"/api/cockpits/{cid}/proxy/")
        assert proxied.status_code == 200
        assert f"/api/cockpits/{cid}/proxy/style.css" in proxied.text
        assert f"/api/cockpits/{cid}/proxy/logo.png" in proxied.text
        assert "x-frame-options" not in {k.lower() for k in proxied.headers}

        # A sub-resource proxies straight through.
        css = client.get(f"/api/cockpits/{cid}/proxy/style.css")
        assert css.status_code == 200
        assert "body{}" in css.text

        assert client.post(f"/api/cockpits/{cid}/stop").json()["state"] == "stopped"
        # Proxying a stopped cockpit is a bad gateway.
        assert client.get(f"/api/cockpits/{cid}/proxy/").status_code == 502


def test_proxy_requires_running_cockpit() -> None:
    app = create_app()
    with TestClient(app) as client:
        cid = client.post(
            "/api/cockpits", json={"name": "Idle", "command": "echo", "port": 4321}
        ).json()["id"]
        assert client.get(f"/api/cockpits/{cid}/proxy/").status_code == 502


# --------------------------------------------------------------------------
# CockpitManager unit tests
# --------------------------------------------------------------------------


async def _wait_manager(mgr: CockpitManager, cid: int, want: str, tries: int = 60) -> str:
    state = mgr.get_status(cid).state
    for _ in range(tries):
        state = mgr.get_status(cid).state
        if state == want:
            return state
        await asyncio.sleep(0.1)
    return state


async def test_manager_detects_crash() -> None:
    mgr = CockpitManager()
    port = _free_port()
    await mgr.start(
        cockpit_id=1,
        command=f'{sys.executable} -c "import sys; sys.exit(3)"',
        port=port,
    )
    assert await _wait_manager(mgr, 1, "crashed") == "crashed"
    status = mgr.get_status(1)
    assert status.exit_code == 3
    assert status.detail and "exited" in status.detail
    await mgr.stop_all()


async def test_manager_ready_then_stop() -> None:
    mgr = CockpitManager()
    port = _free_port()
    # A trivial server that just holds the port open.
    prog = (
        "import socket,time;"
        "s=socket.socket();"
        f"s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
        f"s.bind(('127.0.0.1',{port}));"
        "s.listen();"
        "time.sleep(30)"
    )
    await mgr.start(cockpit_id=2, command=f'{sys.executable} -c "{prog}"', port=port)
    assert await _wait_manager(mgr, 2, "running") == "running"
    assert mgr.running_port(2) == port

    assert (await mgr.stop(2)).state == "stopped"
    assert mgr.running_port(2) is None
    # The port is released once the process group is reaped.
    await mgr.stop_all()


async def test_manager_start_is_idempotent() -> None:
    mgr = CockpitManager()
    port = _free_port()
    prog = (
        "import socket,time;"
        "s=socket.socket();"
        f"s.bind(('127.0.0.1',{port}));"
        "s.listen();"
        "time.sleep(30)"
    )
    first = await mgr.start(cockpit_id=3, command=f'{sys.executable} -c "{prog}"', port=port)
    first_pid = first.pid
    # A second start while alive returns the same process rather than respawning.
    second = await mgr.start(cockpit_id=3, command="echo other", port=port)
    assert second.pid == first_pid
    await mgr.stop_all()


# --------------------------------------------------------------------------
# Autostart
# --------------------------------------------------------------------------


def test_autostart_flag_persists_for_command_cockpits() -> None:
    app = create_app()
    with TestClient(app) as client:
        created = client.post(
            "/api/cockpits",
            json={"name": "Auto", "command": "echo", "port": 7001, "autostart": True},
        ).json()
        assert created["autostart"] is True
        # Toggle it off via PATCH.
        patched = client.patch(f"/api/cockpits/{created['id']}", json={"autostart": False}).json()
        assert patched["autostart"] is False


def test_autostart_ignored_for_url_cockpits() -> None:
    app = create_app()
    with TestClient(app) as client:
        # Even if requested, a url cockpit never autostarts (no process).
        created = client.post(
            "/api/cockpits",
            json={
                "name": "URL",
                "kind": "url",
                "url": "https://example.com",
                "autostart": True,
            },
        ).json()
        assert created["autostart"] is False


async def test_autostart_cockpits_starts_flagged_command_cockpits() -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Cockpit
    from precursor.backend.services.cockpits import autostart_cockpits, get_cockpit_manager

    port = _free_port()
    prog = (
        "import socket,time;"
        "s=socket.socket();"
        f"s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
        f"s.bind(('127.0.0.1',{port}));"
        "s.listen();"
        "time.sleep(30)"
    )
    # Seed one autostart command cockpit, one non-autostart, one autostart url.
    async with SessionLocal() as session:
        auto = Cockpit(
            name="Auto",
            slug="auto-seed",
            kind="command",
            command=f'{sys.executable} -c "{prog}"',
            port=port,
            autostart=True,
        )
        off = Cockpit(
            name="Off",
            slug="off-seed",
            kind="command",
            command="echo nope",
            port=_free_port(),
            autostart=False,
        )
        session.add_all([auto, off])
        await session.commit()
        await session.refresh(auto)
        await session.refresh(off)
        auto_id, off_id = auto.id, off.id

    mgr = get_cockpit_manager()
    try:
        started = await autostart_cockpits()
        assert started == 1
        assert await _wait_manager(mgr, auto_id, "running") == "running"
        # The non-autostart cockpit was never spawned.
        assert mgr.get_status(off_id).state == "stopped"
    finally:
        await mgr.stop_all()
