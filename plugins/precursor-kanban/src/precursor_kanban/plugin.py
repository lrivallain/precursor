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
    # No settings page: which boards to track is managed on the board itself —
    # the header "+" adds one, right-clicking a row removes it. A second surface
    # in Settings would only be the same list, one navigation further away.
    #
    # Read-only board access for the assistant. Registered as "kanban.board" and
    # launched as a stdio subprocess of the running interpreter, so it shares the
    # app's database and credentials.
    registry.add_mcp_server(
        name="board",
        title="Kanban boards",
        module="precursor_kanban.mcp_server",
    )
