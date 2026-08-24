"""Plugin registry contract tests.

These cover the host side of the plugin seam — discovery, descriptor exposure
and router mounting — independently of any particular plugin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.plugins import (
    KIND_SECTION,
    SLOT_APP_SECTION,
    LoadedPlugin,
    PluginRegistry,
    section_extension,
)


def test_section_extension_shape() -> None:
    ext = section_extension(id="board", title="Board")
    assert ext.kind == KIND_SECTION
    assert ext.slot == SLOT_APP_SECTION
    # The id is the section's route and its SPA lookup key, so nothing else
    # needs to travel in the descriptor beyond its default ordering.
    assert ext.id == "board"
    assert ext.config == {"order": 100}


def test_add_section_accepts_an_order_and_extra_config() -> None:
    registry = PluginRegistry()
    registry.plugins["p"] = LoadedPlugin(id="p")
    registry._current = "p"
    registry.add_section(id="board", title="Board", order=10, config={"x": 1})
    registry._current = None
    (ext,) = registry.frontend_extensions
    assert ext.config == {"order": 10, "x": 1}


def test_plugins_endpoint_never_duplicates_descriptors() -> None:
    """Building several apps must not re-run every plugin's ``register``.

    ``get_registry`` is process-wide, so a non-idempotent ``discover`` would
    append each plugin's routers and descriptors again per app — and the SPA
    would then render one section per app ever created in the process.
    """
    with TestClient(create_app()) as client:
        first = client.get("/api/plugins").json()
    with TestClient(create_app()) as client:
        second = client.get("/api/plugins").json()
    assert first == second
    ids = [e["id"] for e in second]
    assert len(ids) == len(set(ids))


def test_plugin_routers_are_reachable_past_the_spa_fallback() -> None:
    """A plugin route must win over the SPA catch-all.

    Routes match in registration order, so mounting plugin routers after the
    ``/{full_path:path}`` fallback silently served index.html for every plugin
    endpoint. Assert a plugin-shaped route registered through the registry is
    still reachable on a fully built app.
    """
    from precursor.backend.plugins import registry as registry_module

    router = APIRouter(prefix="/api/probe-plugin")

    @router.get("/ping")
    async def ping() -> dict[str, Any]:
        return {"ok": True}

    reg = registry_module.get_registry()
    reg.plugins["probe-plugin"] = LoadedPlugin(id="probe-plugin", routers=[router])
    try:
        with TestClient(create_app()) as client:
            r = client.get("/api/probe-plugin/ping")
            assert r.status_code == 200, r.text
            assert r.json() == {"ok": True}
    finally:
        del reg.plugins["probe-plugin"]


def test_installed_reports_distribution_metadata() -> None:
    """The Settings panel needs provenance, not just a descriptor."""
    app = create_app()
    with TestClient(app) as client:
        installed = client.get("/api/plugins/installed").json()
    kanban = next(p for p in installed if p["id"] == "kanban")
    assert kanban["distribution"] == "precursor-kanban"
    assert kanban["version"]
    assert kanban["error"] is None
    assert kanban["enabled"] is True
    assert "/api/github/projects" in kanban["routes"]
    assert [s["name"] for s in kanban["mcp_servers"]] == ["kanban.board"]


def test_disabling_a_plugin_removes_its_ui_api_and_tools() -> None:
    """A toggle has to be total, not just cosmetic."""
    from precursor.backend.services.mcp.client import get_mcp_client_manager

    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/github/projects").status_code != 404
        assert "kanban.board" in get_mcp_client_manager().plugin_entry_names()

        client.put("/api/plugins/installed/kanban", json={"enabled": False})
        try:
            assert client.get("/api/plugins").json() == []
            assert client.get("/api/github/projects").status_code == 404
            assert "kanban.board" not in get_mcp_client_manager().plugin_entry_names()
        finally:
            client.put("/api/plugins/installed/kanban", json={"enabled": True})

        assert client.get("/api/github/projects").status_code != 404
        assert "kanban.board" in get_mcp_client_manager().plugin_entry_names()


def test_plugin_mcp_servers_are_namespaced_and_attributed() -> None:
    app = create_app()
    with TestClient(app) as client:
        servers = client.get("/api/mcp/servers?probe=false").json()
    entry = next(s for s in servers if s["name"] == "kanban.board")
    # Namespaced by plugin id, so two plugins can't collide, and attributed so
    # the UI can say where it came from. Not user-editable.
    assert entry["plugin_id"] == "kanban"
    assert entry["builtin"] is True


def test_add_mcp_server_rejects_ambiguous_launch_spec() -> None:
    registry = PluginRegistry()
    registry.plugins["p"] = LoadedPlugin(id="p")
    registry._current = "p"
    with pytest.raises(ValueError):
        registry.add_mcp_server(name="x", module="a.b", url="https://example.test")


def test_contributions_are_attributed_to_the_registering_plugin() -> None:
    registry = PluginRegistry()
    registry.plugins["p"] = LoadedPlugin(id="p")
    registry._current = "p"
    registry.add_section(id="p", title="P")
    registry.add_mcp_server(name="tools", module="p.mcp")
    registry._current = None
    assert registry.plugins["p"].frontend_extensions[0].id == "p"
    assert registry.plugins["p"].mcp_servers[0].name == "p.tools"


def test_contributing_outside_register_is_refused() -> None:
    """Attribution is not optional — a stray call must fail loudly."""
    registry = PluginRegistry()
    with pytest.raises(RuntimeError):
        registry.add_section(id="x", title="X")


# --- installer guards -------------------------------------------------------
#
# These endpoints run a package's build and import code with the app's own
# privileges, and Precursor has no authentication. The guards below are the only
# thing standing between a drive-by web page and arbitrary code execution, so
# they get explicit tests rather than being left to review.

LOCAL = {"Host": "localhost:8000"}


@pytest.fixture()
def _no_real_installer(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture installer argv instead of running it."""
    from precursor.backend.routers import plugins as plugins_router

    calls: list[list[str]] = []

    async def _fake(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return 0, "ok"

    monkeypatch.setattr(plugins_router, "run_install", _fake)
    return calls


def test_installing_is_off_by_default(_no_real_installer: list[list[str]]) -> None:
    app = create_app()
    with TestClient(app) as client:
        env = client.get("/api/plugins/environment", headers=LOCAL).json()
        assert env["can_install"] is False
        # …but the environment can host an install, so the UI offers the opt-in.
        assert env["installable_here"] is True
        assert (
            client.post("/api/plugins/install", json={"package": "x"}, headers=LOCAL).status_code
            == 403
        )
    assert _no_real_installer == []


@pytest.mark.parametrize(
    "host",
    [
        "attacker.example.com",  # DNS rebinding: resolves to 127.0.0.1
        "localhost.evil.com",
        "127.0.0.1.evil.com",
    ],
)
def test_installer_refuses_a_foreign_host_header(
    host: str, _no_real_installer: list[list[str]]
) -> None:
    """A loopback *bind* is not a boundary — the Host header is.

    A page on an attacker's domain that rebinds to 127.0.0.1 is same-origin to
    the browser, so no CORS preflight ever runs.
    """
    app = create_app()
    with TestClient(app) as client:
        client.put("/api/settings", json={"plugin_install_enabled": True})
        for path, kwargs in (
            ("/api/plugins/install", {"json": {"package": "x"}}),
            ("/api/plugins/restart", {}),
        ):
            r = client.post(path, headers={"Host": host}, **kwargs)  # type: ignore[arg-type]
            assert r.status_code == 403, f"{path} accepted Host: {host}"
        assert (
            client.delete("/api/plugins/installed/kanban", headers={"Host": host}).status_code
            == 403
        )
    assert _no_real_installer == []


def test_installer_refuses_a_cross_site_origin(_no_real_installer: list[list[str]]) -> None:
    """A plain cross-site form POST is CORS-simple — no preflight to rely on."""
    app = create_app()
    with TestClient(app) as client:
        client.put("/api/settings", json={"plugin_install_enabled": True})
        r = client.post(
            "/api/plugins/install",
            json={"package": "x"},
            headers={**LOCAL, "Origin": "https://evil.example.com"},
        )
        assert r.status_code == 403
    assert _no_real_installer == []


@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1:8000", "[::1]:8000", "localhost"])
def test_installer_accepts_this_instances_own_address(
    host: str, _no_real_installer: list[list[str]]
) -> None:
    app = create_app()
    with TestClient(app) as client:
        client.put("/api/settings", json={"plugin_install_enabled": True})
        r = client.post("/api/plugins/install", json={"package": "safe"}, headers={"Host": host})
        assert r.status_code == 200, r.text
        assert r.json()["restart_required"] is True
    assert _no_real_installer and _no_real_installer[-1][-1] == "safe"


def test_uv_tool_environments_are_detected_via_xdg_data_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """uv honours XDG_DATA_HOME for its tool dir; missing it misreports the env.

    A `uv tool` install misdetected as a plain venv gets `uv pip install`, which
    appears to work and is silently discarded on the next tool upgrade.
    """
    from precursor.backend.plugins import install as install_mod

    tools = tmp_path / "uv" / "tools" / "precursor-ai"
    tools.mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("UV_TOOL_DIR", raising=False)
    monkeypatch.setattr(install_mod.sys, "prefix", str(tools))
    assert install_mod.detect_environment().installer == "uv-tool"
    # A uv tool env is rebuilt from its requested packages, so a single one
    # cannot be removed — that has to surface rather than silently "work".
    assert install_mod.uninstall_command("anything") is None
