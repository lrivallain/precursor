"""Plugin-facing API: what's installed, what it contributes, and toggling it.

Split out of ``main.py`` now that ``/api/plugins`` is more than a descriptor
dump: the SPA uses it to decide which sections exist, and the Settings panel
uses it to list installed plugins with their provenance and load failures.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import get_settings
from precursor.backend.db import get_session
from precursor.backend.plugins import get_registry
from precursor.backend.plugins.assets import media_type, plugin_entry_url, resolve_asset
from precursor.backend.plugins.catalog import load_catalog, normalize_distribution
from precursor.backend.plugins.install import (
    Environment,
    detect_environment,
    host_constraints,
    host_upgrade,
    install_command,
    refresh_nightly,
    restart_in_place,
    restart_supported,
    run_install,
    uninstall_command,
)
from precursor.backend.plugins.settings import read_settings, write_settings
from precursor.backend.plugins.sources import (
    Releases,
    Resolution,
    Source,
    SourceError,
    classify,
    installed_source,
    is_newer,
    list_releases,
    requirement_for,
    resolve,
)
from precursor.backend.plugins.state import disabled_ids, is_enabled, set_enabled
from precursor.backend.services.app_settings import resolve_plugin_install_enabled
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.mcp.precursor_server import LOOPBACK_HOSTS, is_loopback_host

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


class PluginToggle(BaseModel):
    enabled: bool


class PluginInstall(BaseModel):
    """What to install.

    ``package`` is a package name, a GitHub repository link, or any PEP 508
    requirement the installer accepts. ``version`` picks a release for the
    first two; left out, a name installs unpinned and a repository installs its
    newest release.
    """

    package: str = Field(min_length=1, max_length=300)
    version: str | None = Field(default=None, max_length=64)


class PluginUpgrade(BaseModel):
    """Move an installed plugin to ``version``, or to its newest release."""

    version: str | None = Field(default=None, max_length=64)


def _require_known_plugin(plugin_id: str) -> None:
    """Refuse settings for a plugin that isn't installed.

    Without this the endpoint is an arbitrary key/value store on the app's
    settings table, writable by anything that can reach the API.
    """
    if plugin_id not in get_registry().plugins:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No plugin named '{plugin_id}'")


def _host_is_local(request: Request) -> bool:
    """Whether the request's ``Host`` names this instance's loopback bind.

    The bind address alone is not a boundary: a page on an attacker's domain
    that DNS-rebinds to 127.0.0.1 is same-origin as far as the browser is
    concerned, so no CORS preflight ever runs. Checking the Host header is what
    actually pins a request to "someone typed localhost", and it is the same
    guard the built-in MCP HTTP endpoint uses (see ``precursor_server``).
    """
    raw = (request.headers.get("host") or "").strip()
    if not raw:
        return False
    try:
        # Parsed as an authority so the port is dropped and a bracketed IPv6
        # literal ("[::1]:8000") is unwrapped correctly — splitting on ":" is
        # not enough for the IPv6 form.
        hostname = urlsplit(f"//{raw}").hostname
    except ValueError:
        return False
    return (hostname or "").lower() in LOOPBACK_HOSTS


def _origin_is_local(request: Request) -> bool:
    """Reject a cross-site caller that reveals itself via ``Origin``.

    A form POST from another site sends `Origin` and needs no preflight, so this
    closes the one hole the Host check alone leaves.
    """
    origin = request.headers.get("origin")
    if not origin:
        return True  # same-origin navigations and non-browser clients
    try:
        host = urlsplit(origin).hostname or ""
    except ValueError:
        return False
    return host.lower() in LOOPBACK_HOSTS


async def _require_local_admin(request: Request, session: AsyncSession) -> None:
    """Guard the mutating installer endpoints.

    Installing a package executes its build and import code with the privileges
    of whoever runs Precursor, so these endpoints are remote code execution by
    design, and Precursor has no authentication of its own. Three layers:

    1. the app must be bound to loopback,
    2. the request must *address* that loopback bind (anti DNS-rebinding) and
       not come from a foreign origin,
    3. the user must have switched the in-app installer on.
    """
    if not is_loopback_host(get_settings().host):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Installing plugins is only available when Precursor is bound to "
            "localhost. Install the package yourself and restart.",
        )
    if not _host_is_local(request) or not _origin_is_local(request):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This endpoint only answers requests addressed to Precursor's own localhost address.",
        )
    if not await resolve_plugin_install_enabled(session):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Installing plugins from inside the app is disabled. Turn it on in "
            "Settings → Plugins, or run the install command yourself.",
        )


async def _github_token(
    request: Request, session: AsyncSession, *sources: Source | None
) -> str | None:
    """The user's GitHub token for a release lookup — only for a local caller.

    Anonymous lookups work for public repositories but share a small rate limit,
    so the token is worth sending. It is also what can see a *private*
    repository's releases, though, and these lookups are readable: a page that
    DNS-rebinds to this instance must not get them answered with the user's
    credentials. So the same Host/Origin check as the installer decides.
    """
    if not any(s is not None and s.kind == "github" for s in sources):
        return None
    if not (_host_is_local(request) and _origin_is_local(request)):
        return None
    return await resolve_github_token(session) or None


def _releases_payload(releases: Releases) -> dict[str, Any]:
    """A source's releases, each with the requirement that would install it."""
    latest = releases.latest
    return {
        "source": releases.source.as_dict(),
        "latest": latest.version if latest else None,
        # What "Latest" installs: a floor at the newest PyPI release, or the
        # newest release's wheel from GitHub.
        "latest_requirement": requirement_for(releases, None),
        "versions": [
            {
                "version": r.version,
                "prerelease": r.prerelease,
                "published_at": r.published_at,
                "tag": r.tag,
                "requirement": requirement_for(releases, r),
            }
            for r in releases.releases
        ],
    }


@router.get("")
async def list_extensions() -> list[dict[str, Any]]:
    """Frontend extension descriptors from every *enabled* plugin.

    This is the boot-time contract the SPA reads: a descriptor here means the
    contribution exists and should be mounted. A disabled or failed plugin
    publishes nothing, which is what makes switching one off remove its UI.
    """
    registry = get_registry()
    out: list[dict[str, Any]] = []
    for plugin in registry.plugins.values():
        if plugin.error is not None or not is_enabled(plugin.id):
            continue
        entry = plugin_entry_url(plugin.id)
        for ext in plugin.frontend_extensions:
            out.append(
                {
                    "id": ext.id,
                    "kind": ext.kind,
                    "slot": ext.slot,
                    "title": ext.title,
                    "config": ext.config,
                    "plugin_id": plugin.id,
                    # URL of the plugin's built ES module, when it ships one.
                    # The SPA imports it before resolving this descriptor.
                    "entry": entry,
                }
            )
    return out


@router.get("/installed")
async def list_installed() -> list[dict[str, Any]]:
    """Every installed plugin, enabled or not, for the Settings panel."""
    registry = get_registry()
    off = disabled_ids()
    return [
        {
            "id": plugin.id,
            "distribution": plugin.distribution,
            "version": plugin.version,
            "summary": plugin.summary,
            "homepage": plugin.homepage,
            "enabled": plugin.id not in off,
            "error": plugin.error,
            "entry": plugin_entry_url(plugin.id),
            "sections": [
                {"id": e.id, "title": e.title}
                for e in plugin.frontend_extensions
                if e.kind == "section"
            ],
            "extensions": [
                {"id": e.id, "kind": e.kind, "slot": e.slot, "title": e.title}
                for e in plugin.frontend_extensions
            ],
            "settings_pages": [
                {"id": e.id, "title": e.title}
                for e in plugin.frontend_extensions
                if e.kind == "settings-page"
            ],
            "routes": plugin.route_prefixes,
            "mcp_servers": [{"name": s.name, "title": s.title} for s in plugin.mcp_servers],
            # Where upgrades come from — read from local metadata, no network.
            "source": installed_source(plugin.distribution).as_dict()
            if plugin.distribution
            else None,
        }
        for plugin in registry.plugins.values()
    ]


@router.get("/catalog")
async def list_catalog() -> list[dict[str, Any]]:
    """The curated directory of plugins that can be installed.

    Bundled with Precursor rather than fetched, so it works offline and every
    entry was reviewed before it shipped — see ``plugins/catalog.py``. Each one
    is annotated with what this instance already has, so the panel can offer
    "Install" or "Installed" without the client re-deriving it.
    """
    installed = get_registry().plugins
    by_distribution = {
        normalize_distribution(p.distribution): p for p in installed.values() if p.distribution
    }
    off = disabled_ids()
    out: list[dict[str, Any]] = []
    for entry in load_catalog():
        # Match on the distribution first: that is what an install actually
        # puts on disk, and it stays true even if a plugin's entry-point name
        # changes underneath the catalogue.
        plugin = by_distribution.get(normalize_distribution(entry.distribution)) or installed.get(
            entry.id
        )
        out.append(
            {
                **entry.as_dict(),
                "installed": plugin is not None,
                "enabled": plugin is not None and plugin.id not in off,
                "installed_version": plugin.version if plugin else None,
            }
        )
    return out


@router.get("/versions")
async def plugin_versions(
    request: Request,
    package: str = Query(min_length=1, max_length=300),
    refresh: bool = False,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Every installable release of a package name or GitHub repository.

    Read-only — nothing is installed — and cached per source, so opening the
    panel doesn't re-ask PyPI or GitHub each time. ``refresh`` skips the cache.
    """
    source = classify(package)
    if source is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Versions can be listed for a package name or a GitHub repository link.",
        )
    token = await _github_token(request, session, source)
    try:
        releases = await list_releases(source, token=token, refresh=refresh)
    except SourceError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return _releases_payload(releases)


@router.get("/updates")
async def plugin_updates(
    request: Request,
    refresh: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """The newest release of every installed plugin, from where it was installed.

    A plugin from PyPI is compared against PyPI, one from a GitHub repository
    against that repository's releases — the version core happens to pin, if
    any, has no say. Lookups run concurrently, and one failing is reported on
    its own row rather than failing the rest.
    """
    plugins = [p for p in get_registry().plugins.values() if p.distribution]
    sources = {p.id: installed_source(p.distribution or "") for p in plugins}
    token = await _github_token(request, session, *sources.values())

    async def check(plugin: Any) -> dict[str, Any]:
        source = sources[plugin.id]
        row: dict[str, Any] = {
            "id": plugin.id,
            "distribution": plugin.distribution,
            "installed_version": plugin.version,
            "source": source.as_dict(),
            "latest": None,
            "update_available": False,
            "upgrade_requirement": None,
            "error": None,
        }
        if source.kind == "direct":
            return row
        try:
            releases = await list_releases(source, token=token, refresh=refresh)
        except SourceError as exc:
            row["error"] = str(exc)
            return row
        row["latest"] = releases.latest.version if releases.latest else None
        if is_newer(releases, plugin.version):
            row["update_available"] = True
            row["upgrade_requirement"] = requirement_for(releases, None)
        return row

    return list(await asyncio.gather(*(check(p) for p in plugins)))


@router.put("/installed/{plugin_id}")
async def toggle_plugin(
    plugin_id: str,
    payload: PluginToggle,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Switch a plugin on or off.

    Takes effect immediately for its API routes, its UI (the descriptors above
    disappear) and its MCP servers, which are re-hydrated here rather than at
    the next restart.
    """
    registry = get_registry()
    plugin = registry.plugins.get(plugin_id)
    if plugin is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No plugin named '{plugin_id}'")
    if payload.enabled and plugin.error is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"'{plugin_id}' failed to load and can't be enabled: {plugin.error}",
        )
    await set_enabled(session, plugin_id, payload.enabled)

    from precursor.backend.plugins.mcp import hydrate_plugin_servers

    hydrate_plugin_servers()
    return {"id": plugin_id, "enabled": payload.enabled}


@router.get("/{plugin_id}/assets/{path:path}", include_in_schema=False)
async def get_plugin_asset(plugin_id: str, path: str) -> FileResponse:
    """Serve a file from an installed plugin's built frontend bundle.

    Read-only and containment-checked (see ``plugins/assets.py``). Disabled
    plugins are refused: their descriptors are already gone, so nothing should
    be importing their module.
    """
    if not is_enabled(plugin_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"The '{plugin_id}' plugin is disabled.")
    resolved = resolve_asset(plugin_id, path)
    if resolved is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset not found")
    return FileResponse(resolved, media_type=media_type(resolved))


@router.get("/installed/{plugin_id}/settings")
async def get_plugin_settings(
    plugin_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """A plugin's own settings blob. Opaque to core."""
    _require_known_plugin(plugin_id)
    return await read_settings(session, plugin_id)


@router.put("/installed/{plugin_id}/settings")
async def put_plugin_settings(
    plugin_id: str,
    values: dict[str, Any],
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Replace a plugin's settings wholesale.

    Whole-document rather than a merge: core has no schema to merge *against*,
    and a plugin removing one of its own keys must be able to say so.
    """
    _require_known_plugin(plugin_id)
    return await write_settings(session, plugin_id, values)


@router.get("/environment")
async def plugin_environment(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """How to install a plugin into *this* instance.

    A `uv tool install` environment can't be extended with `pip install` — the
    tool has to be reinstalled naming the extra package — so the UI shows the
    command that actually works here rather than a generic one.
    """
    # Off the loop: a nightly install's command is built from the published
    # manifest, which may need fetching.
    env = await asyncio.to_thread(detect_environment)
    opted_in = await resolve_plugin_install_enabled(session)
    local = is_loopback_host(get_settings().host) and _host_is_local(request)
    return {
        "installer": env.installer,
        "command_template": env.command_template.format(package="<package>"),
        "python": env.python,
        # Whether the *server* may run it, as opposed to the user running the
        # command themselves — which is always possible.
        "can_install": env.can_install and local and opted_in,
        # True when only the opt-in is missing — i.e. offering the checkbox is
        # meaningful, rather than the environment being unable to install at all.
        "installable_here": env.can_install and local,
        "reason": (
            env.reason
            if not env.can_install
            else None
            if local and opted_in
            # Deliberately doesn't name Settings → Plugins: this string is
            # rendered *in* that panel, directly below the switch it would be
            # pointing at.
            else "Installing from inside the app is off."
            if local
            else "Only available from Precursor's own localhost address."
        ),
        # False only where the process can't bring itself back (e.g. a reload
        # worker started outside the checkout); the UI then asks for a manual one.
        "restart_supported": restart_supported(),
    }


async def _installable_environment() -> Environment:
    """This instance's installer, once it is known to be usable from here."""
    # Fresh, not the display cache: the command about to run is built from it.
    await asyncio.to_thread(refresh_nightly)
    env = detect_environment()
    if not env.can_install:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            env.reason or "This environment can't be modified from inside the app.",
        )
    return env


async def _install(
    resolution: Resolution, env: Environment, *, upgrade: bool = False
) -> dict[str, Any]:
    host = host_upgrade()
    with host_constraints(env) as constraints:
        code, output = await run_install(
            install_command(resolution.requirement, env, upgrade=upgrade, constraints=constraints)
        )
    if code != 0:
        hint = ""
        if resolution.kind == "pypi" and "No solution found" in output:
            # By far the likeliest causes, and both have a way out in the UI.
            hint = (
                "\n\nYour package index may not carry this release yet, or it doesn't "
                "fit this Precursor. Choose another version, or install from the "
                "plugin's GitHub repository."
            )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Install failed:\n{output.strip()[-4000:]}{hint}",
        )
    return {
        "package": resolution.requirement,
        "version": resolution.version,
        "output": output[-4000:],
        "restart_required": True,
        "host_upgrade": host,
    }


@router.post("/install")
async def install_plugin(
    payload: PluginInstall,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Install a package into Precursor's environment, out of process.

    Deliberately does **not** try to load it into the running interpreter:
    entry points are resolved once at startup and routers are mounted while the
    app is built, so a live import would leave a half-installed plugin. The
    caller restarts afterwards.
    """
    await _require_local_admin(request, session)
    env = await _installable_environment()
    try:
        resolution = await resolve(
            payload.package,
            payload.version,
            token=await _github_token(request, session, classify(payload.package)),
        )
    except SourceError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return await _install(resolution, env)


@router.post("/installed/{plugin_id}/upgrade")
async def upgrade_plugin(
    plugin_id: str,
    payload: PluginUpgrade,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Move an installed plugin to another release, from where it came from.

    Only that plugin is allowed to move: Precursor and every other plugin are
    restated as they are, so a plugin upgrade never waits on — or drags along —
    a core release.
    """
    await _require_local_admin(request, session)
    plugin = get_registry().plugins.get(plugin_id)
    if plugin is None or not plugin.distribution:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No installed distribution found for plugin '{plugin_id}'",
        )
    source = installed_source(plugin.distribution)
    if source.kind == "direct":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This plugin was installed from a path or a URL, which has no list of "
            "releases to upgrade from. Reinstall it by name or GitHub repository.",
        )
    env = await _installable_environment()
    try:
        resolution = await resolve(
            source.spec,
            payload.version,
            token=await _github_token(request, session, source),
            distribution=source.distribution,
        )
    except SourceError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return await _install(resolution, env, upgrade=True)


@router.delete("/installed/{plugin_id}")
async def uninstall_plugin(
    plugin_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Remove the distribution behind a plugin, out of process."""
    await _require_local_admin(request, session)
    plugin = get_registry().plugins.get(plugin_id)
    if plugin is None or not plugin.distribution:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No installed distribution found for plugin '{plugin_id}'",
        )
    await asyncio.to_thread(refresh_nightly)
    env = detect_environment()
    if not env.can_install:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            env.reason or "This environment can't be modified from inside the app.",
        )
    argv = uninstall_command(plugin.distribution, env)
    if argv is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This plugin was not installed alongside the tool, so it arrived as "
            "a dependency of Precursor itself or of one of its extras — removing "
            "it would break the install. Disable the plugin instead.",
        )
    upgrade = host_upgrade()
    code, output = await run_install(argv)
    if code != 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Uninstall failed:\n{output.strip()[-4000:]}",
        )
    return {
        "package": plugin.distribution,
        "output": output[-4000:],
        "restart_required": True,
        "host_upgrade": upgrade,
    }


@router.post("/restart", status_code=status.HTTP_202_ACCEPTED)
async def restart(
    background: BackgroundTasks,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Restart Precursor so plugin discovery runs again.

    Scheduled after the response so the caller gets an answer before the
    process is replaced; the SPA polls `/api/health` and reloads when it
    returns.
    """
    await _require_local_admin(request, session)
    if not restart_supported():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This Precursor can't restart itself from here. Restart it the way you started it.",
        )

    async def _later() -> None:
        # Long enough for the response to reach the client and flush.
        await asyncio.sleep(0.5)
        restart_in_place()

    background.add_task(_later)
    return {"status": "restarting"}
