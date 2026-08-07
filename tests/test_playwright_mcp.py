"""Tests for the playwright MCP server registration + npx preflight."""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services.mcp import client as mcp_client


def test_preflight_ok_when_npx_present(monkeypatch) -> None:
    monkeypatch.setattr(mcp_client, "npx_available", lambda: (True, "/usr/bin/npx"))
    assert mcp_client.playwright_preflight_error() is None


def test_preflight_blocks_when_npx_missing(monkeypatch) -> None:
    monkeypatch.setattr(mcp_client, "npx_available", lambda: (False, "npx not found"))
    msg = mcp_client.playwright_preflight_error()
    assert msg is not None
    assert "Node.js" in msg


def test_playwright_registered_as_builtin() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/mcp/servers")
        assert r.status_code == 200
        entry = next((s for s in r.json() if s["name"] == "playwright"), None)
        assert entry is not None
        assert entry["builtin"] is True


def test_playwright_uses_edge_channel_by_default() -> None:
    entry = mcp_client.get_mcp_client_manager().get("playwright")
    assert entry is not None
    assert entry.command == "npx"
    # Edge (msedge) is the default so the server can ride the corporate SSO/WAM
    # broker for authenticated Entra scraping.
    assert entry.args[entry.args.index("--browser") + 1] == "msedge"


def test_playwright_reuses_shared_profile_by_default() -> None:
    entry = mcp_client.get_mcp_client_manager().get("playwright")
    assert entry is not None
    # No override set → don't pin --user-data-dir, so @playwright/mcp reuses its
    # own shared machine-wide profile (any prior sign-in carries over).
    assert "--user-data-dir" not in entry.args


def test_playwright_pins_profile_and_channel_when_overridden(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace

    override = str(tmp_path / "profile")
    monkeypatch.setattr(
        mcp_client,
        "get_settings",
        lambda: SimpleNamespace(playwright_browser="chromium", playwright_profile_dir=override),
    )
    entry = mcp_client.MCPClientManager().get("playwright")
    assert entry is not None
    assert entry.args[entry.args.index("--browser") + 1] == "chromium"
    assert "--user-data-dir" in entry.args
    assert override in entry.args


def test_connect_refuses_when_npx_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(mcp_client, "npx_available", lambda: (False, "npx not found"))
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/api/mcp/servers/playwright/connect")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is False
        assert "Node.js" in (body["error"] or "")


def test_build_playwright_args_default_omits_browser_flag() -> None:
    # "default" (and blank) are the escape hatch for a @playwright/mcp build that
    # rejects --browser with "unknown option" — the flag must be omitted entirely.
    for channel in ("default", "", "  "):
        args = mcp_client.build_playwright_args(
            mcp_client._PLAYWRIGHT_STDIO_ARGS, browser=channel, profile_dir=""
        )
        assert "--browser" not in args


def test_build_playwright_args_includes_channel() -> None:
    args = mcp_client.build_playwright_args(
        mcp_client._PLAYWRIGHT_STDIO_ARGS, browser="firefox", profile_dir=""
    )
    assert args[args.index("--browser") + 1] == "firefox"


def test_configure_playwright_switches_channel_and_resets_state() -> None:
    manager = mcp_client.MCPClientManager()
    manager.configure_playwright(browser="chromium", profile_dir="")
    entry = manager.get("playwright")
    assert entry is not None
    assert entry.args[entry.args.index("--browser") + 1] == "chromium"
    assert entry.state == "disconnected"
    assert entry.tools == []


def test_configure_playwright_default_drops_browser_flag() -> None:
    manager = mcp_client.MCPClientManager()
    # Starts with the msedge default...
    assert "--browser" in manager.get("playwright").args
    # ...and switching to "default" removes the flag so a flag-less build works.
    manager.configure_playwright(browser="default", profile_dir="")
    assert "--browser" not in manager.get("playwright").args


def test_build_playwright_args_drops_flag_when_unsupported() -> None:
    # Even a real channel (msedge) must not append --browser when the resolved
    # build can't accept it — otherwise the server won't start at all.
    args = mcp_client.build_playwright_args(
        mcp_client._PLAYWRIGHT_STDIO_ARGS,
        browser="msedge",
        profile_dir="",
        supports_browser_flag=False,
    )
    assert "--browser" not in args


def test_configure_playwright_unsupported_drops_flag() -> None:
    manager = mcp_client.MCPClientManager()
    manager.configure_playwright(browser="msedge", profile_dir="", supports_browser_flag=False)
    assert "--browser" not in manager.get("playwright").args


async def test_playwright_supports_browser_flag_detects_absence(monkeypatch) -> None:
    # A build whose --help lacks "--browser" (e.g. the stale 1.52 alpha) → False,
    # so the launcher never gets a flag it would reject with "unknown option".
    mcp_client._playwright_browser_flag_support = None
    monkeypatch.setattr(mcp_client, "npx_available", lambda: (True, "/usr/bin/npx"))

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return (b"Usage\n  --headless\n  --vision\n  --help\n", b"")

    async def _fake_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(mcp_client.asyncio, "create_subprocess_exec", _fake_exec)
    assert await mcp_client.playwright_supports_browser_flag() is False


async def test_playwright_supports_browser_flag_detects_presence(monkeypatch) -> None:
    mcp_client._playwright_browser_flag_support = None
    monkeypatch.setattr(mcp_client, "npx_available", lambda: (True, "/usr/bin/npx"))

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return (b"Usage\n  --browser <browser>\n  --headless\n", b"")

    async def _fake_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(mcp_client.asyncio, "create_subprocess_exec", _fake_exec)
    assert await mcp_client.playwright_supports_browser_flag() is True


async def test_playwright_supports_browser_flag_assumes_supported_on_probe_failure(
    monkeypatch,
) -> None:
    # A crashing/absent probe must not strip --browser from healthy builds: on any
    # fault we keep the status quo (assume supported) rather than silently disable.
    mcp_client._playwright_browser_flag_support = None
    monkeypatch.setattr(mcp_client, "npx_available", lambda: (True, "/usr/bin/npx"))

    async def _boom(*args, **kwargs):
        raise OSError("npx blew up")

    monkeypatch.setattr(mcp_client.asyncio, "create_subprocess_exec", _boom)
    assert await mcp_client.playwright_supports_browser_flag() is True


def test_settings_update_reconfigures_playwright_browser() -> None:
    # End-to-end: saving the setting persists it and live-reconfigures the shared
    # built-in entry via the router hook, without a restart.
    app = create_app()
    manager = mcp_client.get_mcp_client_manager()
    try:
        with TestClient(app) as client:
            r = client.put("/api/settings", json={"playwright_browser": "default"})
            assert r.status_code == 200
            assert r.json()["playwright_browser"] == "default"
            assert "--browser" not in manager.get("playwright").args

            r = client.put("/api/settings", json={"playwright_browser": "firefox"})
            assert r.status_code == 200
            assert r.json()["playwright_browser"] == "firefox"
            entry = manager.get("playwright")
            assert entry.args[entry.args.index("--browser") + 1] == "firefox"
    finally:
        # Restore the default so entry state doesn't leak into other tests sharing
        # the singleton manager.
        manager.configure_playwright(browser="msedge", profile_dir="")
