"""Register plugin-contributed MCP servers into the shared client manager.

Kept out of the registry itself so ``discover()`` stays free of app-runtime
imports: hydration happens in the lifespan (and again whenever a plugin is
toggled), by which point the manager exists and the database is readable.
"""

from __future__ import annotations

import logging

from precursor.backend.plugins import get_registry
from precursor.backend.plugins.state import is_enabled
from precursor.backend.services.mcp.client import get_mcp_client_manager

logger = logging.getLogger(__name__)


def hydrate_plugin_servers() -> None:
    """Sync the manager's plugin entries with what's currently enabled.

    Idempotent: registering the same server twice replaces it, and a server
    whose plugin has since been disabled (or failed) is removed, so this doubles
    as the "apply a toggle" path.
    """
    manager = get_mcp_client_manager()
    registry = get_registry()
    wanted: set[str] = set()

    for plugin in registry.plugins.values():
        if plugin.error is not None or not is_enabled(plugin.id):
            continue
        for spec in plugin.mcp_servers:
            try:
                manager.register_plugin_entry(
                    plugin_id=plugin.id,
                    name=spec.name,
                    transport=spec.transport,
                    url=spec.url,
                    command=spec.command,
                    args=list(spec.args),
                    headers=spec.headers,
                    forward_env=spec.forward_env,
                )
            except ValueError:
                # Name collides with a core built-in or a user server; the
                # plugin's other contributions still stand.
                logger.exception("Skipping MCP server '%s' from plugin %s", spec.name, plugin.id)
                continue
            wanted.add(spec.name)

    for name in manager.plugin_entry_names() - wanted:
        manager.unregister_plugin_entry(name)
