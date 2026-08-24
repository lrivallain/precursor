"""Serving a plugin's built frontend bundle out of its installed package.

A plugin ships its compiled ES module inside its own wheel, under ``web/`` in
its import package::

    my_pkg/
      plugin.py
      web/
        index.js        <- the entry the SPA imports
        assets/…        <- chunks, css, images

Precursor exposes that directory read-only at ``/api/plugins/{id}/assets/…`` and
hands the SPA the entry URL in each descriptor. This is what lets a plugin
installed from PyPI contribute UI without being part of core's build.

Resolution goes through ``importlib.resources`` so it works from a zipped wheel
as well as a source checkout, and every request is containment-checked against
the plugin's own directory.
"""

from __future__ import annotations

import logging
import mimetypes
from functools import lru_cache
from importlib import resources
from pathlib import Path

logger = logging.getLogger(__name__)

#: Directory inside a plugin's import package that holds its built bundle.
WEB_DIR = "web"

#: Module the SPA imports to boot a plugin's frontend half.
ENTRY_FILE = "index.js"


@lru_cache(maxsize=64)
def plugin_web_dir(plugin_id: str) -> Path | None:
    """Filesystem path of ``<package>/web`` for a plugin, or ``None``.

    Cached because it is consulted per asset request and the answer only changes
    when packages are installed — which needs a restart anyway.
    """
    from precursor.backend.plugins import get_registry

    plugin = get_registry().plugins.get(plugin_id)
    if plugin is None or not plugin.package:
        return None
    try:
        root = resources.files(plugin.package)
    except (ModuleNotFoundError, TypeError):
        return None
    try:
        # ``as_file`` would copy a zipped resource to a temp dir per call; we
        # only support real directories, which covers wheels installed normally.
        candidate = Path(str(root)) / WEB_DIR
    except (TypeError, ValueError):
        return None
    return candidate if candidate.is_dir() else None


def plugin_entry_url(plugin_id: str) -> str | None:
    """Public URL of a plugin's frontend entry module, if it ships one."""
    web = plugin_web_dir(plugin_id)
    if web is None or not (web / ENTRY_FILE).is_file():
        return None
    return f"/api/plugins/{plugin_id}/assets/{ENTRY_FILE}"


def resolve_asset(plugin_id: str, relative: str) -> Path | None:
    """Resolve a request path to a file inside the plugin's ``web`` directory.

    Returns ``None`` when the plugin ships no bundle, the file is missing, or the
    path escapes the directory (``..``, absolute paths, symlinks pointing out).
    """
    web = plugin_web_dir(plugin_id)
    if web is None:
        return None
    root = web.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def media_type(path: Path) -> str:
    """Content type for an asset, with the JS/CSS cases pinned.

    ``mimetypes`` still reports ``text/javascript`` for ``.js`` on some
    platforms and nothing at all for ``.mjs``; a browser refuses to evaluate a
    module served with the wrong type, so don't leave it to guesswork.
    """
    suffix = path.suffix.lower()
    if suffix in (".js", ".mjs"):
        return "text/javascript"
    if suffix == ".css":
        return "text/css"
    if suffix == ".map" or suffix == ".json":
        return "application/json"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"
