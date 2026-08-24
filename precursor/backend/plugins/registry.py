"""Plugin registry — central place where plugins contribute capabilities."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.metadata import Distribution, distributions, entry_points
from typing import TYPE_CHECKING, Any, Literal

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

#: Extension kind for a plugin's own page in the Settings modal, listed under a
#: "Plugins" group. As with sections, the descriptor only says the page exists;
#: the SPA looks its ``id`` up in the frontend registry for the component.
KIND_SETTINGS_PAGE = "settings-page"

#: Slot a ``settings-page`` extension is rendered into.
SLOT_SETTINGS = "settings.tabs"

MCPTransport = Literal["streamable_http", "stdio"]


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


@dataclass(slots=True)
class PluginMCPServer:
    """An MCP server a plugin contributes to the tool catalogue.

    Mirrors the shape of core's own built-ins: either a hosted
    ``streamable_http`` endpoint or a ``stdio`` subprocess. Because a plugin is
    installed into the app's own environment, the common case is a Python module
    launched with the running interpreter — see
    :meth:`PluginRegistry.add_mcp_server`.
    """

    name: str  # already namespaced as "<plugin_id>.<local name>"
    transport: MCPTransport
    title: str
    url: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    headers: dict[str, str] | None = None
    # Forward the app's environment so an in-process server reaches DB/config.
    forward_env: bool = False


@dataclass(slots=True)
class LoadedPlugin:
    """One installed plugin and everything it contributed.

    ``id`` is the entry-point name, which is also the namespace for anything the
    plugin registers. A plugin whose ``register`` raised is still recorded, with
    ``error`` set, so the UI can show the failure rather than the plugin simply
    being missing.
    """

    id: str
    # Top-level import package the entry point lives in. Frontend assets are
    # resolved relative to it, so a plugin can ship a built bundle in its wheel.
    package: str | None = None
    distribution: str | None = None
    version: str | None = None
    summary: str | None = None
    homepage: str | None = None
    error: str | None = None
    routers: list[APIRouter] = field(default_factory=list)
    frontend_extensions: list[FrontendExtension] = field(default_factory=list)
    mcp_servers: list[PluginMCPServer] = field(default_factory=list)

    @property
    def route_prefixes(self) -> list[str]:
        return [r.prefix for r in self.routers if r.prefix]


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


def settings_page_extension(
    *,
    id: str,
    title: str,
    order: int = 100,
    config: dict[str, Any] | None = None,
) -> FrontendExtension:
    """Build a ``settings-page`` descriptor for a plugin that has configuration.

    ``id`` is the key the SPA looks up to find the React panel, and the key its
    values are stored under (see ``plugins/settings.py``).
    """
    return FrontendExtension(
        id=id,
        kind=KIND_SETTINGS_PAGE,
        slot=SLOT_SETTINGS,
        title=title,
        config={"order": order, **(config or {})},
    )


@dataclass
class PluginRegistry:
    """What every installed plugin contributed, keyed by plugin id.

    A plugin's ``register(registry)`` is called with this object while
    ``_current`` names it, so contributions are attributed automatically without
    the plugin repeating its own id on every call.
    """

    plugins: dict[str, LoadedPlugin] = field(default_factory=dict)
    _current: str | None = None

    # -- contribution API (called from a plugin's ``register``) --------------

    def add_router(self, router: APIRouter) -> None:
        self._plugin().routers.append(router)

    def add_frontend_extension(self, ext: FrontendExtension) -> None:
        self._plugin().frontend_extensions.append(ext)

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

    def add_settings_page(
        self,
        *,
        id: str | None = None,
        title: str | None = None,
        order: int = 100,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Declare that this plugin has settings, and give them a page.

        The page appears in the Settings modal under a **Plugins** group, and the
        values it edits are persisted per plugin at
        ``/api/plugins/installed/{id}/settings`` — a namespaced JSON blob, so a
        plugin never has to touch core's settings schema.

        ``id`` defaults to the plugin's own id, which is the right answer unless
        a plugin wants more than one page.
        """
        plugin = self._plugin()
        self.add_frontend_extension(
            settings_page_extension(
                id=id or plugin.id,
                title=title or plugin.id,
                order=order,
                config=config,
            )
        )

    def add_mcp_server(
        self,
        *,
        name: str,
        title: str | None = None,
        transport: MCPTransport = "stdio",
        module: str | None = None,
        url: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        headers: dict[str, str] | None = None,
        forward_env: bool = True,
    ) -> None:
        """Contribute an MCP server to the tool catalogue.

        The server joins the same catalogue as core's built-ins, so it inherits
        the per-surface enable toggles, probing and tool plumbing with no extra
        wiring. ``name`` is namespaced to ``"<plugin_id>.<name>"`` so two plugins
        can't collide.

        The usual case is a Python module the plugin itself ships::

            registry.add_mcp_server(name="board", module="my_pkg.mcp_server")

        which is launched as ``<running interpreter> -m my_pkg.mcp_server`` with
        the app's environment forwarded, exactly like core's in-tree servers. Pass
        ``url=`` for a hosted ``streamable_http`` endpoint, or ``command``/``args``
        to launch something else entirely.
        """
        plugin = self._plugin()
        if module is not None:
            if command is not None or url is not None:
                raise ValueError("pass exactly one of module=, command= or url=")
            command = sys.executable
            args = ["-m", module]
            transport = "stdio"
        elif url is not None:
            transport = "streamable_http"
        plugin.mcp_servers.append(
            PluginMCPServer(
                name=f"{plugin.id}.{name}",
                transport=transport,
                title=title or name,
                url=url,
                command=command,
                args=list(args or []),
                headers=dict(headers) if headers else None,
                forward_env=forward_env,
            )
        )

    # -- aggregate views ----------------------------------------------------

    @property
    def routers(self) -> list[APIRouter]:
        return [r for p in self.plugins.values() for r in p.routers]

    @property
    def frontend_extensions(self) -> list[FrontendExtension]:
        return [e for p in self.plugins.values() for e in p.frontend_extensions]

    @property
    def mcp_servers(self) -> list[PluginMCPServer]:
        return [s for p in self.plugins.values() for s in p.mcp_servers]

    def plugin_for_extension(self, extension_id: str) -> LoadedPlugin | None:
        for plugin in self.plugins.values():
            if any(e.id == extension_id for e in plugin.frontend_extensions):
                return plugin
        return None

    # -- internals ----------------------------------------------------------

    def _plugin(self) -> LoadedPlugin:
        if self._current is None:
            raise RuntimeError(
                "Plugin contributions must be made from a register(registry) callable"
            )
        return self.plugins[self._current]


@lru_cache
def get_registry() -> PluginRegistry:
    return PluginRegistry()


# Entry points are loaded once per process; the registry they fill is shared.
# Building a second app (tests, or an embedded host) must re-*mount* the routers
# without re-running every plugin's ``register``, which would otherwise duplicate
# its routers and — visibly — its frontend descriptors at ``/api/plugins``.
_loaded = False


@lru_cache(maxsize=1)
def _distribution_by_module() -> dict[str, Distribution]:
    """Map top-level import package -> the distribution that provides it.

    ``EntryPoint.dist`` is only populated on entry points obtained *from* a
    distribution, which isn't guaranteed across importlib versions, so resolve
    it from the module path as a fallback.
    """
    out: dict[str, Distribution] = {}
    for dist in distributions():
        for file in dist.files or ():
            parts = file.parts
            if len(parts) > 1 and not parts[0].endswith((".dist-info", ".egg-info")):
                out.setdefault(parts[0], dist)
    return out


def _describe(ep: Any) -> dict[str, str | None]:
    """Best-effort distribution metadata for the plugin behind ``ep``."""
    dist = getattr(ep, "dist", None)
    if dist is None:
        top_level = str(ep.value).split(":", 1)[0].split(".", 1)[0]
        dist = _distribution_by_module().get(top_level)
    if dist is None:
        return {"distribution": None, "version": None, "summary": None, "homepage": None}
    meta = dist.metadata
    homepage = meta.get("Home-page")
    if not homepage:
        for url in meta.get_all("Project-URL") or ():
            label, _, target = str(url).partition(",")
            if label.strip().lower() in ("homepage", "repository", "source"):
                homepage = target.strip()
                break
    return {
        "distribution": meta.get("Name"),
        "version": dist.version,
        "summary": meta.get("Summary"),
        "homepage": homepage or None,
    }


def discover(app: FastAPI | None = None) -> PluginRegistry:
    """Load every installed plugin and mount its contributions on ``app``.

    Call this while the app is still being built: routes are matched in
    registration order, so a plugin router appended after the SPA catch-all would
    never be reached.
    """
    global _loaded
    registry = get_registry()
    if not _loaded:
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            package = str(ep.value).split(":", 1)[0].split(".", 1)[0] or None
            plugin = LoadedPlugin(id=ep.name, package=package, **_describe(ep))  # type: ignore[arg-type]
            registry.plugins[ep.name] = plugin
            registry._current = ep.name
            try:
                register = ep.load()
                register(registry)
                logger.info("Loaded Precursor plugin: %s", ep.name)
            except Exception as exc:
                plugin.error = f"{type(exc).__name__}: {exc}"
                logger.exception("Failed to load plugin %s", ep.name)
            finally:
                registry._current = None
        _loaded = True

    if app is not None:
        _mount(app, registry)
    return registry


def _mount(app: FastAPI, registry: PluginRegistry) -> None:
    """Include every plugin router, gated on the plugin still being enabled.

    The gate is a route dependency rather than conditional mounting: whether a
    plugin is enabled lives in the database and can change at runtime, long
    after the app was built.
    """
    from fastapi import Depends

    from precursor.backend.plugins.state import require_plugin_enabled

    for plugin in registry.plugins.values():
        guard = Depends(require_plugin_enabled(plugin.id))
        for router in plugin.routers:
            app.include_router(router, dependencies=[guard])
