"""Agents-mode runtime probe and lazy SDK access.

Agents mode depends on ``github-copilot-sdk``, a normal dependency. This module
is still the single seam between Precursor and it: everything here is safe to
import even when the SDK is absent, so a broken or stripped install degrades
gracefully to "Agents unavailable" instead of failing to start.

The SDK wheel is a small ``py3-none-any`` package that **downloads** the native
Copilot CLI on first use (it used to ship platform-specific wheels that bundled
the binary — that line is why the package could not be a default dependency).
Shipping the SDK therefore does not guarantee a runnable runtime, and
``agents_available`` reflects both conditions separately: the Python package is
importable *and* a CLI binary resolves. The CLI is the payload that stays
opt-in, provisioned on an explicit click from Settings → Agents
(:mod:`precursor.backend.services.agents.provision`).

Resolution here is deliberately **read-only** — it reports what is already on
disk and never triggers the SDK's on-demand download. A capability probe runs on
every Settings render, and pulling a ~90 MB binary as a side effect of drawing a
toggle would be indefensible; provisioning belongs behind an explicit user
action.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any

from precursor.backend.config import get_settings

logger = logging.getLogger(__name__)

_SDK_MODULE = "copilot"
_CLI_EXECUTABLE = "copilot"


def sdk_installed() -> bool:
    """True when the ``copilot`` SDK package is importable."""
    try:
        return importlib.util.find_spec(_SDK_MODULE) is not None
    except (ImportError, ValueError):  # pragma: no cover - defensive
        return False


def runtime_binary_path() -> str | None:
    """Resolve a Copilot CLI binary the SDK can drive, or ``None``.

    Layered on purpose, because the SDK's own resolution order (explicit path >
    ``COPILOT_CLI_PATH`` > its download cache) would otherwise *fetch* the binary
    when nothing is cached, and a probe must never do that:

    1. ``COPILOT_CLI_PATH`` — the SDK's documented escape hatch, so an operator
       who pinned a binary sees that exact one reported back.
    2. The SDK's own download cache, which is what it would use next.
    3. A ``copilot`` executable on ``PATH`` — a system-wide CLI install (Homebrew,
       npm, the official installer) is a perfectly good runtime, and ignoring it
       is what made a working machine report "unavailable".
    4. The binary bundled inside pre-1.0.4 platform-specific wheels. Our own
       floor excludes that line, so this only fires for an install that resolved
       the SDK some other way (a shared environment, a stale pin).

    Only steps 2 and 4 reach into SDK internals, and each is independently
    guarded — the env var and ``PATH`` paths keep working even if the SDK moves
    its private symbols again.
    """
    explicit = _existing(os.environ.get("COPILOT_CLI_PATH"))
    if explicit:
        return explicit
    if not sdk_installed():
        return None
    cached = _sdk_cached_cli_path()
    if cached:
        return cached
    # `which` already screens for an executable file. A same-named binary that
    # isn't the Copilot CLI would fail loudly when the manager starts the client,
    # which is a better outcome than declaring the runtime missing outright.
    on_path = shutil.which(_CLI_EXECUTABLE)
    if on_path:
        return on_path
    return _legacy_bundled_cli_path()


def _existing(path: str | None) -> str | None:
    """Return ``path`` only when it still points at something on disk."""
    return path if path and Path(path).exists() else None


def _sdk_cached_cli_path() -> str | None:
    """Path of the CLI the SDK already downloaded, if any.

    Calls ``get_cached_cli_path`` rather than ``get_or_download_cli`` so the
    probe stays read-only. The lookup is pinned to the SDK's expected CLI
    version, so a cache holding only *other* versions correctly reads as a miss.
    """
    try:
        download_mod = importlib.import_module("copilot._cli_download")
        return _existing(download_mod.get_cached_cli_path())
    except Exception:  # pragma: no cover - private API may move between versions
        logger.debug("Could not read the Copilot CLI download cache", exc_info=True)
        return None


def _legacy_bundled_cli_path() -> str | None:
    """Path of the CLI bundled inside pre-1.0.4 SDK wheels, if that line is installed."""
    try:
        client_mod = importlib.import_module("copilot.client")
        return _existing(client_mod._get_bundled_cli_path())
    except Exception:  # pragma: no cover - absent on every current SDK release
        logger.debug("Could not resolve a bundled Copilot CLI path", exc_info=True)
        return None


def agents_available() -> tuple[bool, str]:
    """Return ``(ok, detail)`` — whether the Agents runtime is usable right now.

    Mirrors :func:`services.cmd_runner.docker_available`: a cheap capability
    probe the Settings UI surfaces so the toggle can explain *why* it's
    unavailable. Independent of the user's enabled/disabled preference.

    The two failure modes are not equal. A missing CLI is the ordinary case on a
    fresh install and the panel can fix it in one click. A missing SDK means the
    install itself is broken — it is a declared dependency — so that wording
    points at repairing the install rather than at a step the user forgot.
    """
    if not sdk_installed():
        return False, (
            "github-copilot-sdk is missing from this installation — reinstall "
            "Precursor (it is a required dependency, not an extra)"
        )
    binary = runtime_binary_path()
    if not binary:
        return False, (
            "Copilot CLI runtime not installed yet — install it from "
            "Settings → Agents, or point COPILOT_CLI_PATH at an existing one"
        )
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
            "Agents mode requires 'github-copilot-sdk', which is a required "
            "dependency but is missing from this installation — reinstall Precursor."
        )
    return _import_sdk()


@lru_cache(maxsize=1)
def load_rpc() -> Any:
    """Return ``copilot.generated.rpc`` (permission-decision constructors).

    The ``PermissionDecision*`` classes are *not* re-exported on the top-level
    ``copilot`` module — they live in this generated RPC module. Centralised here
    so the manager has one place to reach them.
    """
    load_sdk()
    return importlib.import_module("copilot.generated.rpc")
