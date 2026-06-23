"""Agents-mode runtime probe and lazy SDK access.

Agents mode is **opt-in** and depends on the optional ``github-copilot-sdk``
package (installed via the ``agents`` extra). This module is the single seam
between Precursor and that optional dependency: everything here is safe to
import even when the SDK is absent, so the rest of the app degrades gracefully
to "Agents unavailable" instead of failing to start.

On the platforms we target, the SDK wheel **bundles** the native Copilot CLI
runtime binary, so installing the extra is all that's required — there is no
separate download step. ``agents_available`` reflects both conditions: the
Python package is importable *and* a runnable CLI binary resolves.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from precursor.backend.config import get_settings

logger = logging.getLogger(__name__)

_SDK_MODULE = "copilot"


def sdk_installed() -> bool:
    """True when the ``copilot`` SDK package is importable."""
    try:
        return importlib.util.find_spec(_SDK_MODULE) is not None
    except (ImportError, ValueError):  # pragma: no cover - defensive
        return False


def runtime_binary_path() -> str | None:
    """Resolve the Copilot CLI binary the SDK would use, or ``None``.

    Order mirrors the SDK: an explicit ``COPILOT_CLI_PATH`` wins, otherwise the
    binary bundled inside the installed wheel.
    """
    explicit = os.environ.get("COPILOT_CLI_PATH")
    if explicit and Path(explicit).exists():
        return explicit
    if not sdk_installed():
        return None
    try:
        client_mod = importlib.import_module("copilot.client")
        resolver = client_mod._get_bundled_cli_path
        path = resolver()
        return path if path and Path(path).exists() else None
    except Exception:  # pragma: no cover - private API may move between versions
        logger.debug("Could not resolve bundled Copilot CLI path", exc_info=True)
        return None


def agents_available() -> tuple[bool, str]:
    """Return ``(ok, detail)`` — whether the Agents runtime is usable right now.

    Mirrors :func:`services.cmd_runner.docker_available`: a cheap capability
    probe the Settings UI surfaces so the toggle can explain *why* it's
    unavailable. Independent of the user's enabled/disabled preference.
    """
    if not sdk_installed():
        return False, "github-copilot-sdk not installed — run `uv sync --extra agents`"
    binary = runtime_binary_path()
    if not binary:
        return False, "Copilot CLI runtime binary not found for this platform"
    return True, f"ready ({binary})"


def agents_home_dir() -> str:
    """Managed ``COPILOT_HOME`` for persisted agent session state.

    The SDK stores each session's durable state here, so it survives restarts
    and is removed/backed up alongside the rest of the app's data dir.
    """
    home = Path(get_settings().agents_home)
    home.mkdir(parents=True, exist_ok=True)
    return str(home)


@lru_cache(maxsize=1)
def _import_sdk() -> Any:
    """Import and cache the ``copilot`` SDK module (raises if unavailable)."""
    return importlib.import_module(_SDK_MODULE)


def load_sdk() -> Any:
    """Return the imported ``copilot`` module, or raise a clear error.

    Call this only from code paths already gated on :func:`agents_available`.
    """
    if not sdk_installed():
        raise RuntimeError(
            "Agents mode requires the optional 'github-copilot-sdk' package "
            "(install with `uv sync --extra agents`)."
        )
    return _import_sdk()
