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


@dataclass(slots=True)
class FrontendExtension:
    """Descriptor a plugin contributes to the SPA.

    The frontend reads ``/api/plugins`` and maps ``kind`` + ``slot`` to a
    component in its own plugin registry (see ``frontend/src/lib/plugins.ts``).
    """

    id: str
    kind: str  # e.g. "panel", "message-renderer", "settings-tab"
    slot: str  # where the SPA should render it
    title: str
    # Arbitrary JSON-serializable config the plugin wants to ship to the client.
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PluginRegistry:
    routers: list[APIRouter] = field(default_factory=list)
    frontend_extensions: list[FrontendExtension] = field(default_factory=list)
    mcp_tools: list[dict[str, Any]] = field(default_factory=list)

    def add_router(self, router: APIRouter) -> None:
        self.routers.append(router)

    def add_frontend_extension(self, ext: FrontendExtension) -> None:
        self.frontend_extensions.append(ext)

    def add_mcp_tool(self, tool: dict[str, Any]) -> None:
        self.mcp_tools.append(tool)


@lru_cache
def get_registry() -> PluginRegistry:
    return PluginRegistry()


def discover(app: FastAPI | None = None) -> PluginRegistry:
    """Load every installed plugin and let it register its contributions."""
    registry = get_registry()
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            register = ep.load()
            register(registry)
            logger.info("Loaded Precursor plugin: %s", ep.name)
        except Exception:
            logger.exception("Failed to load plugin %s", ep.name)

    if app is not None:
        for router in registry.routers:
            app.include_router(router)
    return registry
