"""Agents-mode runtime probe and lazy SDK access.

Agents mode depends on ``microsoft-copilot-sdk``, a normal dependency. This module
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
every Settings render, and pulling a ~100 MB runtime as a side effect of drawing a
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

    Only step 2 reaches into SDK internals, and it is guarded: the env var and
    ``PATH`` steps keep working even if the SDK moves its private symbols again.
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
    return shutil.which(_CLI_EXECUTABLE)


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
            "microsoft-copilot-sdk is missing from this installation — reinstall "
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
            "Agents mode requires 'microsoft-copilot-sdk', which is a required "
            "dependency but is missing from this installation — reinstall Precursor."
        )
    return _import_sdk()


@lru_cache(maxsize=1)
def load_rpc() -> Any:
    """Return ``copilot.rpc`` (permission decisions, history requests).

    These classes are *not* re-exported on the top-level ``copilot`` module.
    """
    load_sdk()
    return importlib.import_module("copilot.rpc")


# The SDK's own levels; anything else in ``PRECURSOR_LOG_LEVEL`` maps onto them.
_SDK_LOG_LEVELS = {"none", "error", "warning", "info", "debug", "all"}
_LOG_LEVEL_ALIASES = {"warn": "warning", "critical": "error", "fatal": "error", "trace": "all"}


def sdk_log_level(level: str) -> str:
    """Map Precursor's log level onto one the SDK's runtime accepts."""
    level = level.strip().lower()
    level = _LOG_LEVEL_ALIASES.get(level, level)
    return level if level in _SDK_LOG_LEVELS else "info"


def runtime_env() -> dict[str, str]:
    """Environment for the spawned CLI, pinned to the binary the probe resolved.

    Left to itself the SDK resolves ``COPILOT_CLI_PATH`` and then *downloads* the
    CLI when its cache is empty, so a machine whose only Copilot CLI is on
    ``PATH`` would pass the capability probe and then pull the runtime at
    startup, and could even drive a different binary than the one Settings
    reported. Handing the probe's answer back keeps the two in agreement and
    keeps startup off the network.
    """
    env = dict(os.environ)
    resolved = runtime_binary_path()
    if resolved:
        env["COPILOT_CLI_PATH"] = resolved
    return env


def build_client(sdk: Any) -> Any:
    """A ``CopilotClient`` that spawns the probed CLI over stdio.

    Spawned rather than in-process on purpose: in-process hosting needs the
    SDK's own ``runtime.node`` next to the binary, which a ``COPILOT_CLI_PATH``
    or ``PATH`` install doesn't have. Session state lives in the managed
    :func:`agents_home_dir`.
    """
    env = runtime_env()
    return sdk.CopilotClient(
        mode="coding-agent",
        runtime=sdk.RuntimeConnection.for_spawned_stdio(
            path=env.get("COPILOT_CLI_PATH"),
            env=env,
            base_directory=agents_home_dir(),
        ),
        observability={"log_level": sdk_log_level(get_settings().log_level)},
    )
