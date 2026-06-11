"""Plugin discovery + lightweight extension registry.

Plugins are regular Python packages that declare an entry point::

    [project.entry-points."precursor.plugins"]
    my_plugin = "my_pkg.precursor_plugin:register"

The ``register`` callable receives a :class:`PluginRegistry` instance and can:

* mount additional FastAPI routers
* register MCP tools
* contribute frontend extension descriptors (panels / renderers) that the SPA
  fetches at startup via ``GET /api/plugins``

This module is intentionally minimal — the goal is to fix the *contract* now so
plugin authors can target a stable surface even before all hooks are wired.
"""

from precursor.backend.plugins.registry import (
    FrontendExtension,
    PluginRegistry,
    discover,
    get_registry,
)

__all__ = ["FrontendExtension", "PluginRegistry", "discover", "get_registry"]
