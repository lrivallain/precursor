"""Plugin registry contract tests.

These cover the host side of the plugin seam — discovery, descriptor exposure
and router mounting — independently of any particular plugin.

Nothing here installs one. Every plugin Precursor ships with, ``precursor-kanban``
included, lives in its own repository, so the suite would otherwise be asserting
against whatever happened to be in the environment. The contributions below are
registered through the same API a real ``register(registry)`` uses, which keeps
attribution, namespacing and mounting under test with nothing to install.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.plugins import (
    KIND_SECTION,
    KIND_SETTINGS_PAGE,
    SLOT_APP_SECTION,
    SLOT_SETTINGS,
    LoadedPlugin,
    PluginRegistry,
    section_extension,
    settings_page_extension,
)

STUB_ID = "stub"
STUB_PREFIX = f"/api/{STUB_ID}"


class _FakeEntryPoint:
    """Minimal stand-in for an ``importlib.metadata.EntryPoint``.

    ``discover`` only needs ``name``, ``value`` and ``load()``; ``dist`` stays
    ``None`` so provenance falls through to the module-path lookup, exactly as it
    does for a real plugin whose entry points came from ``entry_points(group=…)``.
    """

    dist = None

    def __init__(self, name: str, value: str, register: Any) -> None:
        self.name = name
        self.value = value
        self._register = register

    def load(self) -> Any:
        return self._register


def _contribute(registry: PluginRegistry) -> None:
    """A plugin's ``register`` — one of each kind of contribution."""
    router = APIRouter(prefix=STUB_PREFIX)

    @router.get("/ping")
    async def ping() -> dict[str, Any]:
        return {"ok": True}

    registry.add_router(router)
    registry.add_section(id=STUB_ID, title="Stub")
    registry.add_mcp_server(name="tools", title="Stub tools", module="stub.mcp_server")


@pytest.fixture()
def stub_plugin() -> Iterator[LoadedPlugin]:
    """Register a plugin the way ``discover`` does, then take it back out.

    Injected rather than discovered because ``discover`` loads entry points once
    per process: by the time any test runs, that has already happened.
    """
    from precursor.backend.plugins import registry as registry_module

    reg = registry_module.get_registry()
    plugin = LoadedPlugin(id=STUB_ID, package=STUB_ID, distribution="stub-plugin", version="1.0")
    reg.plugins[STUB_ID] = plugin
    reg._current = STUB_ID
    try:
        _contribute(reg)
    finally:
        reg._current = None
    try:
        yield plugin
    finally:
        del reg.plugins[STUB_ID]


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


def test_plugins_endpoint_never_duplicates_descriptors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Building several apps must not re-run every plugin's ``register``.

    ``get_registry`` is process-wide, so a non-idempotent ``discover`` would
    append each plugin's routers and descriptors again per app — and the SPA
    would then render one section per app ever created in the process.

    Driven through a fake entry point, and with ``_loaded`` reset so discovery
    genuinely re-runs: no plugin distribution is installed here, so relying on
    whatever the environment provides would make this assert nothing.
    """
    from precursor.backend.plugins import registry as registry_module

    ep = _FakeEntryPoint(STUB_ID, f"{STUB_ID}.plugin:register", _contribute)
    monkeypatch.setattr(registry_module, "entry_points", lambda **_: [ep])
    monkeypatch.setattr(registry_module, "_loaded", False)

    reg = registry_module.get_registry()
    try:
        with TestClient(create_app()) as client:
            first = client.get("/api/plugins").json()
        with TestClient(create_app()) as client:
            second = client.get("/api/plugins").json()
        assert first == second
        assert [e["id"] for e in second] == [STUB_ID]
        # An id is unique *per kind*, not globally: a plugin's section and its
        # settings page share the plugin's id by design, and each is looked up in
        # its own frontend registry.
        keys = [(e["id"], e["kind"]) for e in second]
        assert len(keys) == len(set(keys))
    finally:
        reg.plugins.pop(STUB_ID, None)


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


def test_installed_reports_a_plugins_contributions(stub_plugin: LoadedPlugin) -> None:
    """The Settings panel needs provenance and a contribution inventory."""
    app = create_app()
    with TestClient(app) as client:
        installed = client.get("/api/plugins/installed").json()
    entry = next(p for p in installed if p["id"] == STUB_ID)
    assert entry["distribution"] == "stub-plugin"
    assert entry["version"] == "1.0"
    assert entry["error"] is None
    assert entry["enabled"] is True
    assert STUB_PREFIX in entry["routes"]
    assert [s["name"] for s in entry["mcp_servers"]] == [f"{STUB_ID}.tools"]


def test_distribution_metadata_is_resolved_from_the_module_path() -> None:
    """Provenance must survive an ``EntryPoint`` that carries no ``dist``.

    ``EntryPoint.dist`` is only populated on entry points obtained *from* a
    distribution, so the registry falls back to mapping the entry point's
    top-level module onto the distribution that installed it. Asserted against
    ``fastapi``: a hard dependency, so it is always present, and always a regular
    wheel install — an *editable* one (which is how this project installs itself)
    ships no file list to map back from.
    """
    from precursor.backend.plugins.registry import _describe

    meta = _describe(_FakeEntryPoint("probe", "fastapi:register", None))
    assert meta["distribution"] == "fastapi"
    assert meta["version"]


def test_disabling_a_plugin_removes_its_ui_api_and_tools(stub_plugin: LoadedPlugin) -> None:
    """A toggle has to be total, not just cosmetic."""
    from precursor.backend.services.mcp.client import get_mcp_client_manager

    server = f"{STUB_ID}.tools"
    app = create_app()
    with TestClient(app) as client:
        assert client.get(f"{STUB_PREFIX}/ping").status_code == 200
        assert server in get_mcp_client_manager().plugin_entry_names()

        client.put(f"/api/plugins/installed/{STUB_ID}", json={"enabled": False})
        try:
            assert client.get("/api/plugins").json() == []
            assert client.get(f"{STUB_PREFIX}/ping").status_code == 404
            assert server not in get_mcp_client_manager().plugin_entry_names()
        finally:
            client.put(f"/api/plugins/installed/{STUB_ID}", json={"enabled": True})

        assert client.get(f"{STUB_PREFIX}/ping").status_code == 200
        assert server in get_mcp_client_manager().plugin_entry_names()


def test_plugin_mcp_servers_are_namespaced_and_attributed(stub_plugin: LoadedPlugin) -> None:
    app = create_app()
    with TestClient(app) as client:
        servers = client.get("/api/mcp/servers?probe=false").json()
    entry = next(s for s in servers if s["name"] == f"{STUB_ID}.tools")
    # Namespaced by plugin id, so two plugins can't collide, and attributed so
    # the UI can say where it came from. Not user-editable.
    assert entry["plugin_id"] == STUB_ID
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


@pytest.fixture(autouse=True)
def _environment_never_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the in-app installer on every platform, Windows included.

    On a real Windows install it is withheld (see the dedicated test), but the
    installer tests below are about the commands, not the platform.
    """
    from precursor.backend.plugins import install as install_mod

    monkeypatch.setattr(install_mod, "_environment_locked_while_running", lambda: False)


@pytest.fixture(autouse=True)
def _no_real_indexes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep release lookups off the network: every index answers 404.

    An install by name looks the newest release up to set its floor, and a
    missing answer falls back to the bare name — which is what the installer
    tests below expect unless they stub an index themselves.
    """
    import httpx

    from precursor.backend.plugins import sources

    sources.invalidate()
    original = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        # `setdefault`: a test that stubs an index wraps this factory, and its
        # transport has to win.
        kwargs.setdefault("transport", httpx.MockTransport(lambda _r: httpx.Response(404)))
        return original(*args, **kwargs)

    monkeypatch.setattr(sources.httpx, "AsyncClient", factory)


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
            client.delete(f"/api/plugins/installed/{STUB_ID}", headers={"Host": host}).status_code
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


def test_a_windows_tool_install_is_not_rebuilt_from_inside_the_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Windows won't delete files the running app holds open, so the command is
    shown for the user to run once Precursor is stopped instead."""
    _uv_tool_env(monkeypatch, tmp_path)
    from precursor.backend.plugins import install as install_mod

    monkeypatch.setattr(install_mod, "_environment_locked_while_running", lambda: True)
    env = install_mod.detect_environment()
    assert env.installer == "uv-tool"
    assert env.can_install is False
    assert env.reason is not None and "precursor service stop" in env.reason
    assert "uv tool install" in env.command_template


def test_uv_tool_environments_are_detected_via_xdg_data_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """uv honours XDG_DATA_HOME for its tool dir; missing it misreports the env.

    A `uv tool` install misdetected as a plain venv gets `uv pip install`, which
    appears to work and is silently discarded on the next tool upgrade.
    """
    _uv_tool_env(monkeypatch, tmp_path)
    from precursor.backend.plugins import install as install_mod

    assert install_mod.detect_environment().installer == "uv-tool"
    # Nothing was installed alongside the tool, so this package can only have
    # arrived as a dependency — removing it would break the install.
    assert install_mod.uninstall_command("anything") is None


# --- extending a uv tool environment ----------------------------------------
#
# `uv tool install` rewrites the receipt from its own arguments. So every
# command that touches the environment has to restate the whole of it: the
# host with its extras and pinned wheel, plus every plugin already installed
# beside it. Naming less is not "leave the rest alone", it is "uninstall it".

_WHEEL = "https://example.invalid/nightly/precursor_ai-2026.9-py3-none-any.whl"


def _uv_tool_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, receipt: str | None = None
) -> Path:
    from precursor.backend.plugins import install as install_mod

    tools = tmp_path / "uv" / "tools" / "precursor-ai"
    tools.mkdir(parents=True, exist_ok=True)
    if receipt is not None:
        (tools / "uv-receipt.toml").write_text(receipt, encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("UV_TOOL_DIR", raising=False)
    monkeypatch.setattr(install_mod.sys, "prefix", str(tools))
    return tools


def _receipt(*entries: str) -> str:
    return "[tool]\nrequirements = [\n" + "".join(f"    {e},\n" for e in entries) + "]\n"


def test_installing_a_plugin_does_not_downgrade_the_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bug this guards: the command named a bare `precursor-ai`, so adding a
    plugin dropped the extras (uninstalling the tray) and re-resolved a pinned
    nightly down to whatever the index served as latest."""
    from precursor.backend.plugins import install as install_mod

    _uv_tool_env(
        monkeypatch,
        tmp_path,
        _receipt(f'{{ name = "precursor-ai", extras = ["tray"], url = "{_WHEEL}" }}'),
    )
    argv = install_mod.install_command("precursor-notes")

    assert f"precursor-ai[tray] @ {_WHEEL}" in argv
    assert "precursor-ai" not in argv
    assert argv[-2:] == ["--with", "precursor-notes"]


def test_installing_a_second_plugin_keeps_the_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from precursor.backend.plugins import install as install_mod

    _uv_tool_env(
        monkeypatch,
        tmp_path,
        _receipt('{ name = "precursor-ai" }', '{ name = "precursor-kanban" }'),
    )
    argv = install_mod.install_command("precursor-notes")

    assert argv.count("--with") == 2
    assert "precursor-kanban" in argv and "precursor-notes" in argv


def test_reinstalling_a_plugin_names_it_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Upgrading an installed plugin must not ask uv for it twice."""
    from precursor.backend.plugins import install as install_mod

    _uv_tool_env(
        monkeypatch,
        tmp_path,
        _receipt('{ name = "precursor-ai" }', '{ name = "precursor_kanban" }'),
    )
    argv = install_mod.install_command("precursor-kanban")
    assert argv.count("--with") == 1


def test_uninstalling_rebuilds_the_tool_without_that_plugin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The receipt says what the tool was requested with, so "reinstall without
    this one" is expressible exactly rather than refused."""
    from precursor.backend.plugins import install as install_mod

    _uv_tool_env(
        monkeypatch,
        tmp_path,
        _receipt(
            '{ name = "precursor-ai", extras = ["tray"] }',
            '{ name = "precursor-kanban" }',
            '{ name = "precursor-notes" }',
        ),
    )
    argv = install_mod.uninstall_command("precursor-kanban")

    assert argv is not None
    assert "precursor-kanban" not in argv
    assert argv.count("--with") == 1 and "precursor-notes" in argv
    assert "precursor-ai[tray]" in argv


def test_the_displayed_command_matches_what_the_server_would_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With the in-app installer off, this string is the only path a user has —
    so it must not be a narrower command than the one the server runs."""
    from precursor.backend.plugins import install as install_mod

    _uv_tool_env(
        monkeypatch,
        tmp_path,
        _receipt(f'{{ name = "precursor-ai", extras = ["tray"], url = "{_WHEEL}" }}'),
    )
    rendered = install_mod.detect_environment().command_template.format(package="precursor-notes")

    assert rendered.startswith("uv tool install --force ")
    assert f"precursor-ai[tray] @ {_WHEEL}" in rendered
    assert rendered.endswith("--with precursor-notes")


# The nightly release is deleted and re-created on every push to main, so a
# receipt pinned to one of its wheels names a 404 as soon as anything newer
# ships. Restating that pin failed every plugin install on a nightly host.


def _nightly(name: str) -> str:
    from precursor.backend.config import get_settings

    return f"https://github.com/{get_settings().update_repo}/releases/download/nightly/{name}"


_OLD_HOST = _nightly("precursor_ai-2026.9.1.dev75+gd01924e81-py3-none-any.whl")
_NEW_HOST = _nightly("precursor_ai-2026.9.1.dev77+g347650f90-py3-none-any.whl")


def _publish(monkeypatch: pytest.MonkeyPatch, *extra: str) -> list[bool]:
    """Stand in for the nightly manifest; records each lookup's ``force`` flag."""
    from precursor.backend.services import updates

    lookups: list[bool] = []

    def _published(*, force: bool = False) -> updates.NightlyBuild:
        lookups.append(force)
        return updates.NightlyBuild(
            version="2026.9.1.dev77+g347650f90", wheel_url=_NEW_HOST, extra_wheel_urls=extra
        )

    monkeypatch.setattr(updates, "published_nightly", _published)
    return lookups


def test_a_superseded_nightly_host_is_restated_as_the_published_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from precursor.backend.plugins import install as install_mod

    _uv_tool_env(
        monkeypatch,
        tmp_path,
        _receipt(f'{{ name = "precursor-ai", extras = ["tray"], url = "{_OLD_HOST}" }}'),
    )
    _publish(monkeypatch)

    argv = install_mod.install_command("precursor-kanban")
    assert f"precursor-ai[tray] @ {_NEW_HOST}" in argv
    assert not any(_OLD_HOST in a for a in argv)
    # Still pinned: a bare name would drop to whatever the index serves.
    assert "precursor-ai[tray]" not in argv
    # The user is told Precursor itself moves, rather than finding out later.
    assert install_mod.host_upgrade() == "2026.9.1.dev77+g347650f90"
    # And the command shown for running by hand is the same, working one.
    template = install_mod.detect_environment().command_template
    assert _NEW_HOST in template and _OLD_HOST not in template


def test_superseded_nightly_companions_follow_the_release_or_the_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from precursor.backend.plugins import install as install_mod

    new_notes = _nightly("precursor_notes-0.2-py3-none-any.whl")
    _uv_tool_env(
        monkeypatch,
        tmp_path,
        _receipt(
            f'{{ name = "precursor-ai", url = "{_OLD_HOST}" }}',
            f'{{ name = "precursor-notes", url = "{_nightly("precursor_notes-0.1-py3-none-any.whl")}" }}',
            f'{{ name = "precursor-mail", url = "{_nightly("precursor_mail-0.1-py3-none-any.whl")}" }}',
            '{ name = "precursor-kanban" }',
        ),
    )
    _publish(monkeypatch, new_notes)

    argv = install_mod.uninstall_command("precursor-kanban")
    assert argv is not None
    assert f"precursor-ai @ {_NEW_HOST}" in argv
    assert f"precursor-notes @ {new_notes}" in argv
    # No longer on the release at all: resolved from the index, not a 404.
    assert "precursor-mail" in argv
    assert "precursor-kanban" not in argv


def test_a_current_nightly_pin_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from precursor.backend.plugins import install as install_mod

    _uv_tool_env(
        monkeypatch, tmp_path, _receipt(f'{{ name = "precursor-ai", url = "{_NEW_HOST}" }}')
    )
    _publish(monkeypatch)

    assert f"precursor-ai @ {_NEW_HOST}" in install_mod.install_command("precursor-kanban")
    assert install_mod.host_upgrade() is None


def test_an_unreadable_manifest_keeps_the_pin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from precursor.backend.plugins import install as install_mod
    from precursor.backend.services import updates

    _uv_tool_env(
        monkeypatch, tmp_path, _receipt(f'{{ name = "precursor-ai", url = "{_OLD_HOST}" }}')
    )
    monkeypatch.setattr(updates, "published_nightly", lambda *, force=False: None)

    assert f"precursor-ai @ {_OLD_HOST}" in install_mod.install_command("precursor-kanban")
    assert install_mod.host_upgrade() is None


def test_only_a_nightly_pin_costs_a_manifest_lookup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stable or index install must not reach out to GitHub to build a command."""
    from precursor.backend.plugins import install as install_mod

    _uv_tool_env(
        monkeypatch,
        tmp_path,
        _receipt(
            f'{{ name = "precursor-ai", extras = ["tray"], url = "{_WHEEL}" }}',
            '{ name = "precursor-kanban" }',
        ),
    )
    lookups = _publish(monkeypatch)

    install_mod.refresh_nightly()
    install_mod.install_command("precursor-notes")
    install_mod.detect_environment()
    assert install_mod.host_upgrade() is None
    assert lookups == []


def test_the_installer_reads_the_manifest_fresh_and_reports_the_host_move(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_real_installer: list[list[str]]
) -> None:
    """The display path may use a cached manifest; running a command may not —
    the release can have been republished since."""
    from precursor.backend.plugins import install as install_mod

    _uv_tool_env(
        monkeypatch,
        tmp_path,
        _receipt(f'{{ name = "precursor-ai", extras = ["tray"], url = "{_OLD_HOST}" }}'),
    )
    monkeypatch.setattr(install_mod, "_uv", lambda: "/usr/bin/uv")
    lookups = _publish(monkeypatch)

    app = create_app()
    with TestClient(app) as client:
        client.put("/api/settings", json={"plugin_install_enabled": True})
        lookups.clear()
        r = client.post("/api/plugins/install", json={"package": "precursor-kanban"}, headers=LOCAL)
        assert r.status_code == 200, r.text
        assert r.json()["host_upgrade"] == "2026.9.1.dev77+g347650f90"

    assert lookups[0] is True
    assert f"precursor-ai[tray] @ {_NEW_HOST}" in _no_real_installer[-1]


# --- versions, sources and upgrades -------------------------------------------
#
# Plugins version independently of core: an install names a package or a GitHub
# repository (optionally a version), and an upgrade moves one plugin — never the
# host. Network is stubbed at the HTTP layer, below everything under test.

_NOTES_WHEEL = (
    "https://github.com/acme/precursor-notes/releases/download/{tag}/"
    "precursor_notes-{version}-py3-none-any.whl"
)


def _stub_indexes(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """PyPI knows stub-plugin 1.0/2.0; GitHub knows acme/precursor-notes 1.0.0/2.0.0."""
    import httpx

    from precursor.backend.plugins import sources

    sources.invalidate()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "pypi.org" and request.url.path == "/pypi/stub-plugin/json":
            return httpx.Response(
                200,
                json={
                    "info": {"name": "stub-plugin", "version": "2.0"},
                    "releases": {
                        "1.0": [{"upload_time_iso_8601": "2026-01-01T00:00:00Z"}],
                        "2.0": [{"upload_time_iso_8601": "2026-02-01T00:00:00Z"}],
                    },
                },
            )
        if request.url.path == "/repos/acme/precursor-notes/releases":
            return httpx.Response(
                200,
                json=[
                    {
                        "tag_name": f"v{v}",
                        "published_at": f"2026-0{v[0]}-01T00:00:00Z",
                        "assets": [
                            {
                                "name": f"precursor_notes-{v}-py3-none-any.whl",
                                "browser_download_url": _NOTES_WHEEL.format(tag=f"v{v}", version=v),
                            }
                        ],
                    }
                    for v in ("2.0.0", "1.0.0")
                ],
            )
        return httpx.Response(404)

    original = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(sources.httpx, "AsyncClient", factory)
    return seen


def _installing(client: TestClient) -> None:
    client.put("/api/settings", json={"plugin_install_enabled": True})


def test_versions_are_listed_for_a_name_or_a_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_indexes(monkeypatch)
    with TestClient(create_app()) as client:
        pypi = client.get("/api/plugins/versions", params={"package": "stub-plugin"}).json()
        github = client.get(
            "/api/plugins/versions", params={"package": "https://github.com/acme/precursor-notes"}
        ).json()
        missing = client.get("/api/plugins/versions", params={"package": "nope-nope"})
        freeform = client.get("/api/plugins/versions", params={"package": "x>=1"})

    assert pypi["source"]["kind"] == "pypi"
    assert pypi["latest"] == "2.0"
    # "Latest" from PyPI is a floor; a chosen version is pinned.
    assert pypi["latest_requirement"] == "stub-plugin>=2.0"
    assert [v["requirement"] for v in pypi["versions"]] == ["stub-plugin==2.0", "stub-plugin==1.0"]

    assert github["source"] == {
        "kind": "github",
        "distribution": "precursor-notes",
        "repository": "acme/precursor-notes",
        "specifier": "",
        "pinned": False,
    }
    assert github["latest_requirement"] == "precursor-notes @ " + _NOTES_WHEEL.format(
        tag="v2.0.0", version="2.0.0"
    )
    assert missing.status_code == 404
    assert freeform.status_code == 400


def test_the_github_token_only_answers_a_local_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rebinding page could otherwise read a private repository's releases
    with the user's credentials."""
    from precursor.backend.routers import plugins as plugins_router

    seen = _stub_indexes(monkeypatch)

    async def _token(_session: Any) -> str:
        return "secret"

    monkeypatch.setattr(plugins_router, "resolve_github_token", _token)
    params = {"package": "github.com/acme/precursor-notes", "refresh": "true"}
    with TestClient(create_app()) as client:
        client.get("/api/plugins/versions", params=params, headers=LOCAL)
        client.get("/api/plugins/versions", params=params, headers={"Host": "attacker.example"})

    assert seen[0].headers.get("authorization") == "Bearer secret"
    assert "authorization" not in seen[1].headers


def test_installing_a_chosen_version_pins_it(
    monkeypatch: pytest.MonkeyPatch, _no_real_installer: list[list[str]]
) -> None:
    _stub_indexes(monkeypatch)
    with TestClient(create_app()) as client:
        _installing(client)
        r = client.post(
            "/api/plugins/install",
            json={"package": "stub-plugin", "version": "1.0"},
            headers=LOCAL,
        )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == "1.0"
    assert _no_real_installer[-1][-1] == "stub-plugin==1.0"
    assert "--upgrade-package" not in _no_real_installer[-1]


def test_a_github_repository_installs_its_release_wheel(
    monkeypatch: pytest.MonkeyPatch, _no_real_installer: list[list[str]]
) -> None:
    _stub_indexes(monkeypatch)
    repo = "https://github.com/acme/precursor-notes"
    with TestClient(create_app()) as client:
        _installing(client)
        latest = client.post("/api/plugins/install", json={"package": repo}, headers=LOCAL)
        older = client.post(
            "/api/plugins/install", json={"package": repo, "version": "1.0.0"}, headers=LOCAL
        )
        absent = client.post(
            "/api/plugins/install", json={"package": repo, "version": "9.0.0"}, headers=LOCAL
        )

    assert latest.status_code == 200 and older.status_code == 200
    wheels = [argv[-1] for argv in _no_real_installer]
    assert wheels == [
        "precursor-notes @ " + _NOTES_WHEEL.format(tag="v2.0.0", version="2.0.0"),
        "precursor-notes @ " + _NOTES_WHEEL.format(tag="v1.0.0", version="1.0.0"),
    ]
    assert absent.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"package": "stub-plugin", "version": "1.0 --index-url https://evil.example"},
        {"package": "stub-plugin>=1", "version": "1.0"},
    ],
)
def test_a_bad_version_request_never_reaches_the_installer(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, str], _no_real_installer: list[list[str]]
) -> None:
    _stub_indexes(monkeypatch)
    with TestClient(create_app()) as client:
        _installing(client)
        r = client.post("/api/plugins/install", json=payload, headers=LOCAL)
    assert r.status_code == 400
    assert _no_real_installer == []


def test_updates_compare_each_plugin_with_its_own_source(
    monkeypatch: pytest.MonkeyPatch, stub_plugin: LoadedPlugin
) -> None:
    _stub_indexes(monkeypatch)
    with TestClient(create_app()) as client:
        rows = client.get("/api/plugins/updates").json()
        installed = client.get("/api/plugins/installed").json()

    (row,) = [r for r in rows if r["id"] == STUB_ID]
    assert row["source"]["kind"] == "pypi"
    assert row["installed_version"] == "1.0"
    assert row["latest"] == "2.0"
    assert row["update_available"] is True
    assert row["upgrade_requirement"] == "stub-plugin>=2.0"
    (listed,) = [p for p in installed if p["id"] == STUB_ID]
    assert listed["source"]["kind"] == "pypi"


def test_upgrading_moves_only_that_plugin(
    monkeypatch: pytest.MonkeyPatch,
    stub_plugin: LoadedPlugin,
    _no_real_installer: list[list[str]],
) -> None:
    _stub_indexes(monkeypatch)
    with TestClient(create_app()) as client:
        _installing(client)
        latest = client.post(f"/api/plugins/installed/{STUB_ID}/upgrade", json={}, headers=LOCAL)
        chosen = client.post(
            f"/api/plugins/installed/{STUB_ID}/upgrade", json={"version": "1.0"}, headers=LOCAL
        )

    assert latest.status_code == 200, latest.text
    assert latest.json()["version"] == "2.0"
    first, second = _no_real_installer
    assert first[-1] == "stub-plugin>=2.0"
    assert first[first.index("--upgrade-package") + 1] == "stub-plugin"
    # Choosing a version — a downgrade included — pins exactly that one.
    assert chosen.status_code == 200
    assert second[-1] == "stub-plugin==1.0"


def test_upgrading_is_gated_like_installing(
    monkeypatch: pytest.MonkeyPatch,
    stub_plugin: LoadedPlugin,
    _no_real_installer: list[list[str]],
) -> None:
    _stub_indexes(monkeypatch)
    with TestClient(create_app()) as client:
        client.put("/api/settings", json={"plugin_install_enabled": False})
        off = client.post(f"/api/plugins/installed/{STUB_ID}/upgrade", json={}, headers=LOCAL)
        _installing(client)
        foreign = client.post(
            f"/api/plugins/installed/{STUB_ID}/upgrade", json={}, headers={"Host": "evil.example"}
        )
    assert off.status_code == 403 and foreign.status_code == 403
    assert _no_real_installer == []


def test_a_plugin_installed_from_a_path_has_nothing_to_upgrade_from(
    monkeypatch: pytest.MonkeyPatch,
    stub_plugin: LoadedPlugin,
    _no_real_installer: list[list[str]],
) -> None:
    from precursor.backend.plugins import sources
    from precursor.backend.routers import plugins as plugins_router

    monkeypatch.setattr(plugins_router, "installed_source", lambda d: sources.Source("direct", d))
    with TestClient(create_app()) as client:
        _installing(client)
        r = client.post(f"/api/plugins/installed/{STUB_ID}/upgrade", json={}, headers=LOCAL)
    assert r.status_code == 409
    assert _no_real_installer == []


# --- per-plugin settings ----------------------------------------------------


def test_settings_page_extension_shape() -> None:
    ext = settings_page_extension(id="board", title="Board")
    assert ext.kind == KIND_SETTINGS_PAGE
    assert ext.slot == SLOT_SETTINGS
    assert ext.config == {"order": 100}


def test_add_settings_page_defaults_to_the_plugin_id() -> None:
    registry = PluginRegistry()
    registry.plugins["my-plugin"] = LoadedPlugin(id="my-plugin")
    registry._current = "my-plugin"
    registry.add_settings_page(title="My plugin")
    registry._current = None
    (ext,) = registry.frontend_extensions
    assert ext.id == "my-plugin"


def test_plugin_settings_round_trip_and_are_namespaced(stub_plugin: LoadedPlugin) -> None:
    """Each plugin owns one opaque blob; core never interprets it."""
    from precursor.backend.plugins.settings import settings_key

    assert settings_key(STUB_ID) == f"plugin.{STUB_ID}"

    base = f"/api/plugins/installed/{STUB_ID}/settings"
    app = create_app()
    with TestClient(app) as client:
        # Start from a known state rather than assuming nothing else has written
        # here — the test database is shared across the whole session.
        assert client.put(base, json={}).json() == {}
        assert client.get(base).json() == {}
        stored = {"project_sources": ["acme"], "anything": {"nested": True}}
        assert client.put(base, json=stored).json() == stored
        assert client.get(base).json() == stored
        # Whole-document, so a plugin can remove its own keys.
        assert client.put(base, json={}).json() == {}
        assert client.get(base).json() == {}


def test_settings_are_refused_for_an_unknown_plugin() -> None:
    """Otherwise this is an open key/value store on the app's settings table."""
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/plugins/installed/nope/settings").status_code == 404
        assert client.put("/api/plugins/installed/nope/settings", json={}).status_code == 404


async def test_unreadable_stored_settings_degrade_to_empty() -> None:
    """A bad blob should cost that plugin its config, not 500 every request."""
    import json

    from precursor.backend.db import SessionLocal, init_db
    from precursor.backend.models import AppSetting
    from precursor.backend.plugins.settings import read_settings, settings_key

    # This test touches the database directly rather than through the app, so it
    # has to run the migrations the lifespan would normally have run.
    await init_db()
    key = settings_key(STUB_ID)
    try:
        async with SessionLocal() as session:
            # merge, not add: an earlier test in this module may have left a row
            # under the same key, and a blind insert would collide.
            await session.merge(AppSetting(key=key, value="{not json"))
            await session.commit()
        async with SessionLocal() as session:
            assert await read_settings(session, STUB_ID) == {}
            # A valid JSON *scalar* is just as unusable as broken JSON.
            row = await session.get(AppSetting, key)
            assert row is not None
            row.value = json.dumps(["a", "list"])
            await session.commit()
        async with SessionLocal() as session:
            assert await read_settings(session, STUB_ID) == {}
    finally:
        async with SessionLocal() as session:
            row = await session.get(AppSetting, key)
            if row is not None:
                await session.delete(row)
                await session.commit()


def test_uv_pip_installs_hold_precursors_own_requirements(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression this guards: `uv pip install <plugin>` resolves only the
    plugin, so a release built for MCP 1 downgraded core's `mcp` and broke the
    app. Precursor's requirements go in as constraints; its extras don't, so a
    core extra can't decide which plugin release the user may pick."""
    from precursor.backend.plugins import install as install_mod

    env = install_mod.Environment(
        installer="uv-venv", command_template="", python=sys.executable, can_install=True
    )
    with install_mod.host_constraints(env) as path:
        assert path is not None
        lines = path.read_text(encoding="utf-8").splitlines()
        argv = install_mod.install_command("precursor-notes", env, constraints=path)
    assert not path.exists()

    assert argv[argv.index("--constraint") + 1] == str(path)
    assert argv[-1] == "precursor-notes"
    assert lines[0].startswith("precursor-ai==")
    assert any(line.startswith("mcp") for line in lines)
    assert not any("extra ==" in line for line in lines)


def test_uv_tool_installs_need_no_constraints_file() -> None:
    """They restate the host, so its requirements are already in the resolution."""
    from precursor.backend.plugins import install as install_mod

    env = install_mod.Environment(
        installer="uv-tool", command_template="", python=sys.executable, can_install=True
    )
    with install_mod.host_constraints(env) as path:
        assert path is None


def test_a_failed_resolution_from_pypi_points_at_the_ways_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from precursor.backend.routers import plugins as plugins_router

    async def _unresolvable(_argv: list[str]) -> tuple[int, str]:
        return 1, "No solution found when resolving dependencies"

    monkeypatch.setattr(plugins_router, "run_install", _unresolvable)
    with TestClient(create_app()) as client:
        _installing(client)
        r = client.post(
            "/api/plugins/install",
            json={"package": "stub-plugin", "version": "1.0"},
            headers=LOCAL,
        )
    assert r.status_code == 400
    assert "GitHub repository" in r.json()["detail"]


# --- restarting ---------------------------------------------------------------
#
# "Restart now" re-execs the process. Under `precursor --dev` the app runs in
# uvicorn's reload worker, a multiprocessing child whose argv is the spawn
# handshake — re-exec'ing it died with "Bad file descriptor". There, the restart
# is a change to a file the reloader watches.


def test_the_dev_worker_restarts_through_its_reload_trigger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import os

    from precursor.backend.plugins import install as install_mod

    trigger = tmp_path / "__init__.py"
    trigger.write_text('"""Backend."""\n', encoding="utf-8")
    os.utime(trigger, (0, 0))
    monkeypatch.setenv(install_mod.RELOAD_TRIGGER_ENV, str(trigger))
    monkeypatch.setattr(install_mod.os, "execv", lambda *_: pytest.fail("must not exec"))

    assert install_mod.restart_supported() is True
    install_mod.restart_in_place()

    assert trigger.read_text(encoding="utf-8") == '"""Backend."""\n'
    assert trigger.stat().st_mtime > 0


def test_a_multiprocessing_child_without_a_trigger_does_not_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import multiprocessing

    from precursor.backend.plugins import install as install_mod

    monkeypatch.delenv(install_mod.RELOAD_TRIGGER_ENV, raising=False)
    monkeypatch.setattr(multiprocessing, "parent_process", lambda: object())
    monkeypatch.setattr(install_mod.os, "execv", lambda *_: pytest.fail("must not exec"))

    assert install_mod.restart_supported() is False
    with pytest.raises(RuntimeError):
        install_mod.restart_in_place()


def test_restart_is_refused_up_front_where_it_cant_work(monkeypatch: pytest.MonkeyPatch) -> None:
    from precursor.backend.routers import plugins as plugins_router

    monkeypatch.setattr(plugins_router, "restart_supported", lambda: False)
    monkeypatch.setattr(plugins_router, "restart_in_place", lambda: pytest.fail("ran"))
    with TestClient(create_app()) as client:
        _installing(client)
        assert (
            client.get("/api/plugins/environment", headers=LOCAL).json()["restart_supported"]
            is False
        )
        assert client.post("/api/plugins/restart", headers=LOCAL).status_code == 409


def test_dev_mode_points_the_worker_at_a_watched_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """uvicorn watches the working directory, so the trigger must sit under it."""
    import os

    from precursor.backend import __main__ as cli
    from precursor.backend.plugins import install as install_mod

    repo = Path(cli.__file__).resolve().parents[2]
    monkeypatch.delenv(install_mod.RELOAD_TRIGGER_ENV, raising=False)

    monkeypatch.chdir(repo)
    cli._inject_reload_trigger()
    trigger = Path(os.environ[install_mod.RELOAD_TRIGGER_ENV])
    assert trigger == Path(cli.__file__).resolve().parent / "__init__.py"

    monkeypatch.chdir(tmp_path)
    cli._inject_reload_trigger()
    assert install_mod.RELOAD_TRIGGER_ENV not in os.environ
