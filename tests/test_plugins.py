"""Plugin registry contract tests.

These cover the host side of the plugin seam — discovery, descriptor exposure
and router mounting — independently of any particular plugin.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.plugins import (
    KIND_SECTION,
    SLOT_APP_SECTION,
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
    registry.add_section(id="board", title="Board", order=10, config={"x": 1})
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
    reg.routers.append(router)
    try:
        with TestClient(create_app()) as client:
            r = client.get("/api/probe-plugin/ping")
            assert r.status_code == 200, r.text
            assert r.json() == {"ok": True}
    finally:
        reg.routers.remove(router)
