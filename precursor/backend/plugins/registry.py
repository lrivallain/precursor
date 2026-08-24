"""Plugin registry — central place where plugins contribute capabilities."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "precursor.plugins"

#: Extension kind for a full application section: its own entry in the sidebar
#: rail, a card on the home screen, a command-palette entry and a top-level
#: route at ``/<id>``. The SPA looks the descriptor's ``id`` up in its own
#: section registry (``frontend/src/lib/plugins.ts``) to find the React
#: components to mount, so the descriptor carries only what the backend
#: actually owns: whether the section exists at all, and its default ordering.
KIND_SECTION = "section"

#: Slot a ``section`` extension is rendered into.
SLOT_APP_SECTION = "app.section"


@dataclass(slots=True)
class FrontendExtension:
    """Descriptor a plugin contributes to the SPA.

    The frontend reads ``/api/plugins`` and maps ``kind`` + ``slot`` to a
    component in its own plugin registry (see ``frontend/src/lib/plugins.ts``).
    """

    id: str
    kind: str  # e.g. "section", "panel", "message-renderer", "settings-tab"
    slot: str  # where the SPA should render it
    title: str
    # Arbitrary JSON-serializable config the plugin wants to ship to the client.
    config: dict[str, Any] = field(default_factory=dict)


def section_extension(
    *,
    id: str,
    title: str,
    order: int = 100,
    config: dict[str, Any] | None = None,
) -> FrontendExtension:
    """Build a ``section`` descriptor for a plugin that owns a whole surface.

    ``id`` doubles as the section's top-level URL segment, so it must be
    URL-safe. ``order`` positions it in the default sidebar rail arrangement
    (core sections occupy 0-99, so plugins land after them unless they ask for
    an earlier slot). Everything else the SPA needs -- icon, palette, the React
    components -- is declared in the frontend half of the plugin, keyed by the
    same ``id``.
    """
    return FrontendExtension(
        id=id,
        kind=KIND_SECTION,
        slot=SLOT_APP_SECTION,
        title=title,
        config={"order": order, **(config or {})},
    )


@dataclass(slots=True)
class PluginRegistry:
    routers: list[APIRouter] = field(default_factory=list)
    frontend_extensions: list[FrontendExtension] = field(default_factory=list)
    mcp_tools: list[dict[str, Any]] = field(default_factory=list)

    def add_router(self, router: APIRouter) -> None:
        self.routers.append(router)

    def add_frontend_extension(self, ext: FrontendExtension) -> None:
        self.frontend_extensions.append(ext)

    def add_section(
        self,
        *,
        id: str,
        title: str,
        order: int = 100,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Contribute a top-level application section (sidebar entry + route)."""
        self.add_frontend_extension(
            section_extension(id=id, title=title, order=order, config=config)
        )

    def add_mcp_tool(self, tool: dict[str, Any]) -> None:
        self.mcp_tools.append(tool)


@lru_cache
def get_registry() -> PluginRegistry:
    return PluginRegistry()


# Entry points are loaded once per process; the registry they fill is shared.
# Building a second app (tests, or an embedded host) must re-*mount* the routers
# without re-running every plugin's ``register``, which would otherwise duplicate
# its routers and — visibly — its frontend descriptors at ``/api/plugins``.
_loaded = False


def discover(app: FastAPI | None = None) -> PluginRegistry:
    """Load every installed plugin and mount its contributions on ``app``.

    Call this while the app is still being built: routers are appended in
    registration order, so a plugin registered after the SPA catch-all would
    never be reached.
    """
    global _loaded
    registry = get_registry()
    if not _loaded:
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            try:
                register = ep.load()
                register(registry)
                logger.info("Loaded Precursor plugin: %s", ep.name)
            except Exception:
                logger.exception("Failed to load plugin %s", ep.name)
        _loaded = True

    if app is not None:
        for router in registry.routers:
            app.include_router(router)
    return registry
