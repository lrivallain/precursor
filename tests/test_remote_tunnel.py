"""The dev tunnel manager and the Mobile access routes (issue #521, PR 2).

A fake ``devtunnel`` script on PATH stands in for Microsoft's CLI.
"""

from __future__ import annotations

import asyncio
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services.background_poll import BackgroundPoll
from precursor.backend.services.remote_access import sessions as store
from precursor.backend.services.remote_access import tunnel as tunnel_mod
from precursor.backend.services.remote_access.pairing import pairing_codes
from precursor.backend.services.remote_access.tunnel import TunnelManager
from tests.support import set_app_setting

FAKE_CLI = """#!/bin/sh
state="$FAKE_DEVTUNNEL_DIR"
case "$1 $2" in
  "user show")
    [ -f "$state/signed_out" ] && { echo "Not logged in."; exit 1; }
    echo "Logged in as tester using Microsoft." ;;
  "port create") echo "ok" ;;
  "token "*) echo "Token: tok-123" ;;
  *)
    case "$1" in
      show) [ -f "$state/tunnel-$2" ] || exit 1 ;;
      create) touch "$state/tunnel-$2"; echo "created $2" ;;
      host)
        echo "$2" >> "$state/hosted"
        echo "Hosting port: 8000"
        echo "Connect via browser: https://$2-8000.euw.devtunnels.ms"
        echo "Ready to accept connections for tunnel: $2.euw"
        exec sleep 600 ;;
    esac ;;
esac
"""


@pytest.fixture()
def fake_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if sys.platform == "win32":
        pytest.skip("the fake devtunnel is a POSIX shell script")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "devtunnel"
    exe.write_text(FAKE_CLI)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("FAKE_DEVTUNNEL_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    BackgroundPoll._instances.pop(TunnelManager, None)
    asyncio.run(set_app_setting(tunnel_mod.SETTING_KEY, {}))
    asyncio.run(set_app_setting(store.SETTING_KEY, {"enabled": False, "sessions": []}))
    pairing_codes.cancel()
    yield
    BackgroundPoll._instances.pop(TunnelManager, None)
    asyncio.run(set_app_setting(store.SETTING_KEY, {"enabled": False, "sessions": []}))


async def _until_active(manager: TunnelManager) -> None:
    for _ in range(100):
        if manager.state() == "active":
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"tunnel stuck in {manager.state()}")


def test_missing_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    assert TunnelManager().state() == "missing_cli"


async def test_signed_out(fake_cli: Path) -> None:
    (fake_cli / "signed_out").touch()
    manager = TunnelManager()
    await manager.open()
    assert manager.state() == "signed_out"


async def test_open_creates_a_tunnel_once_and_reports_its_url(fake_cli: Path) -> None:
    manager = TunnelManager()
    await manager.open()
    await _until_active(manager)
    assert manager.url is not None and manager.url.endswith("-8000.euw.devtunnels.ms")
    first = manager.url
    await manager.close()
    assert manager.state() == "stopped"
    # The id is kept: the address (and the phone's icon) survives a restart.
    await manager.open()
    await _until_active(manager)
    assert manager.url == first
    assert len(list(fake_cli.glob("tunnel-*"))) == 1
    await manager.close()


async def test_watchdog_restarts_after_two_failed_probes(
    fake_cli: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = TunnelManager()
    await manager.open()
    await _until_active(manager)

    async def unreachable() -> bool:
        return False

    monkeypatch.setattr(manager, "probe", unreachable)
    await manager.run_once()
    assert (fake_cli / "hosted").read_text().count("\n") == 1
    await manager.run_once()
    await _until_active(manager)
    assert (fake_cli / "hosted").read_text().count("\n") == 2
    await manager.close()


async def test_watchdog_restarts_a_dead_host(fake_cli: Path) -> None:
    manager = TunnelManager()
    await manager.open()
    await _until_active(manager)
    assert manager._proc is not None
    manager._proc.kill()
    await manager._proc.wait()
    await manager.run_once()
    await _until_active(manager)
    assert (fake_cli / "hosted").read_text().count("\n") == 2
    await manager.close()


async def test_watchdog_does_nothing_once_closed(fake_cli: Path) -> None:
    manager = TunnelManager()
    await manager.open()
    await manager.close()
    await manager.run_once()
    assert manager.state() == "stopped"


def test_routes_start_the_tunnel_and_hand_out_a_qr_code(fake_cli: Path) -> None:
    with TestClient(create_app(), base_url="http://localhost") as client:
        assert client.get("/api/remote/tunnel").json()["state"] == "stopped"
        assert client.put("/api/remote/tunnel", json={"running": True}).status_code == 200
        # Starting the tunnel always turns pairing enforcement on.
        assert client.get("/api/remote/access").json()["enabled"] is True
        for _ in range(100):
            status = client.get("/api/remote/tunnel").json()
            if status["state"] == "active":
                break
            asyncio.run(asyncio.sleep(0.05))
        assert status["state"] == "active"
        code = client.post("/api/remote/pairing").json()
        assert code["url"].startswith(status["url"] + "/api/remote/pair?code=")
        # Standalone SVG: shown as an <img> data URI, which needs the namespace.
        assert code["qr_svg"].startswith("<svg")
        assert 'xmlns="http://www.w3.org/2000/svg"' in code["qr_svg"]
        stopped = client.put("/api/remote/tunnel", json={"running": False, "revoke_sessions": True})
        assert stopped.json()["state"] == "stopped"


def test_tunnel_routes_are_local_only(fake_cli: Path) -> None:
    tunnel = {"X-Forwarded-For": "203.0.113.7", "Host": "localhost"}
    with TestClient(create_app(), base_url="http://localhost") as client:
        assert client.get("/api/remote/tunnel", headers=tunnel).status_code == 404
        r = client.put("/api/remote/tunnel", json={"running": True}, headers=tunnel)
        assert r.status_code == 404


def test_health_is_open_to_the_watchdog() -> None:
    tunnel = {"X-Forwarded-For": "203.0.113.7"}
    with TestClient(create_app(), base_url="http://localhost") as client:
        client.put("/api/remote/access", json={"enabled": True})
        assert client.get("/api/remote/health", headers=tunnel).json() == {"ok": True}
