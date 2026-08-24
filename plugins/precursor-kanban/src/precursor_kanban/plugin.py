"""Entry point Precursor calls to wire the kanban plugin in."""

from __future__ import annotations

from precursor.plugin_api import PluginRegistry
from precursor_kanban.router import router

#: Must match the ``id`` the SPA registers its section under
#: (``frontend/src/plugins/kanban/index.tsx``). It is also the section's
#: top-level route, i.e. ``/kanban``.
SECTION_ID = "kanban"


def register(registry: PluginRegistry) -> None:
    registry.add_router(router)
    registry.add_section(id=SECTION_ID, title="Kanban", order=100)
