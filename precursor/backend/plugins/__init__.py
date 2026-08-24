"""Plugin discovery + lightweight extension registry.

Plugins are regular Python packages that declare an entry point::

    [project.entry-points."precursor.plugins"]
    my_plugin = "my_pkg.plugin:register"

The ``register`` callable receives a :class:`PluginRegistry` instance and can:

* mount additional FastAPI routers
* register MCP tools
* contribute frontend extension descriptors that the SPA fetches at startup via
  ``GET /api/plugins`` — including a whole application *section* (sidebar entry,
  home card, command-palette entry and top-level route) via
  :meth:`PluginRegistry.add_section`

Plugin authors should import from :mod:`precursor.plugin_api` rather than from
this package directly; that module is the surface we keep stable.
"""

from precursor.backend.plugins.registry import (
    KIND_SECTION,
    KIND_SETTINGS_PAGE,
    SLOT_APP_SECTION,
    SLOT_SETTINGS,
    FrontendExtension,
    LoadedPlugin,
    PluginMCPServer,
    PluginRegistry,
    discover,
    get_registry,
    section_extension,
    settings_page_extension,
)

__all__ = [
    "KIND_SECTION",
    "KIND_SETTINGS_PAGE",
    "SLOT_APP_SECTION",
    "SLOT_SETTINGS",
    "FrontendExtension",
    "LoadedPlugin",
    "PluginMCPServer",
    "PluginRegistry",
    "discover",
    "get_registry",
    "section_extension",
    "settings_page_extension",
]
