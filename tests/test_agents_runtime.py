"""Copilot CLI resolution — the capability probe behind Agents mode.

The probe is the seam that decides whether Settings offers Agents mode at all,
and it broke silently once already: ``github-copilot-sdk`` 1.0.11 swapped its
platform-specific bundled-binary wheels for a pure-Python wheel that downloads
the CLI on first use, which moved the private symbol the probe reached for. The
whole lookup sat inside ``except Exception``, so a perfectly good install just
reported "runtime binary not found".

These tests pin the layering that replaced it — and, just as importantly, that
the probe stays *read-only*: it must never trigger the SDK's download.

The dev venv has no ``agents`` extra, so the SDK is faked. That's the point:
each test states which of the four sources exists and asserts which one wins.
"""

from __future__ import annotations

import importlib.machinery
import os
import stat
import sys
import types
from pathlib import Path

import pytest

from precursor.backend.services.agents import runtime

_MISSING = object()


@pytest.fixture(autouse=True)
def _neutral_environment(monkeypatch, tmp_path):
    """Hide the developer's own Copilot CLI so tests see only what they install.

    Without this, a machine with a system-wide ``copilot`` (Homebrew, npm) would
    resolve at the ``PATH`` step and quietly pass the tests that assert a *miss*.
    """
    monkeypatch.delenv("COPILOT_CLI_PATH", raising=False)
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def _fake_sdk(monkeypatch, *, cached: str | None | object = _MISSING) -> None:
    """Install a fake ``copilot`` package so ``sdk_installed()`` reports True.

    ``cached`` mirrors what the SDK's download cache would answer; ``_MISSING``
    omits ``copilot._cli_download`` entirely, reproducing an SDK line that
    predates it.
    """
    pkg = types.ModuleType("copilot")
    pkg.__spec__ = importlib.machinery.ModuleSpec("copilot", loader=None, is_package=True)
    pkg.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "copilot", pkg)
    # The probe must resolve without ever reaching the downloader, so leave a
    # landmine on the symbol that would fetch ~90 MB.
    monkeypatch.setitem(sys.modules, "copilot.client", types.ModuleType("copilot.client"))
    if cached is _MISSING:
        monkeypatch.setitem(sys.modules, "copilot._cli_download", None)
        return
    download = types.ModuleType("copilot._cli_download")
    download.get_cached_cli_path = lambda version=None: cached  # type: ignore[attr-defined]
    download.get_or_download_cli = _never_download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "copilot._cli_download", download)


def _never_download(*_args, **_kwargs):  # pragma: no cover - guard, must not run
    raise AssertionError("the capability probe must not download the Copilot CLI")


def _executable(path: Path) -> str:
    """Create a runnable stub at ``path`` and return it as a string."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def test_explicit_env_path_wins(monkeypatch, tmp_path) -> None:
    pinned = _executable(tmp_path / "pinned" / "copilot")
    _fake_sdk(monkeypatch, cached=_executable(tmp_path / "cache" / "copilot"))
    monkeypatch.setenv("COPILOT_CLI_PATH", pinned)

    assert runtime.runtime_binary_path() == pinned


def test_stale_env_path_falls_through(monkeypatch, tmp_path) -> None:
    """A ``COPILOT_CLI_PATH`` left over from an uninstall must not win."""
    cached = _executable(tmp_path / "cache" / "copilot")
    _fake_sdk(monkeypatch, cached=cached)
    monkeypatch.setenv("COPILOT_CLI_PATH", str(tmp_path / "gone" / "copilot"))

    assert runtime.runtime_binary_path() == cached


def test_no_sdk_means_no_binary(monkeypatch, tmp_path) -> None:
    _executable(tmp_path / "bin" / "copilot")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setattr(runtime, "sdk_installed", lambda: False)

    assert runtime.runtime_binary_path() is None
    ok, detail = runtime.agents_available()
    assert ok is False
    assert "github-copilot-sdk not installed" in detail


def test_sdk_cache_beats_path(monkeypatch, tmp_path) -> None:
    cached = _executable(tmp_path / "cache" / "copilot")
    _executable(tmp_path / "bin" / "copilot")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    _fake_sdk(monkeypatch, cached=cached)

    assert runtime.runtime_binary_path() == cached


@pytest.mark.skipif(sys.platform == "win32", reason="PATH lookup needs a suffixed exe on Windows")
def test_system_cli_on_path_is_used_when_cache_is_empty(monkeypatch, tmp_path) -> None:
    """The regression: a system-wide CLI install is a usable runtime.

    The SDK's cache is keyed by the exact CLI version it pins, so a machine
    holding only *other* versions reads as a miss — which is how a box with
    ``/opt/homebrew/bin/copilot`` still reported the runtime as unavailable.
    """
    on_path = _executable(tmp_path / "bin" / "copilot")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    _fake_sdk(monkeypatch, cached=None)

    assert runtime.runtime_binary_path() == on_path
    ok, detail = runtime.agents_available()
    assert ok is True
    assert on_path in detail


@pytest.mark.skipif(sys.platform == "win32", reason="PATH lookup needs a suffixed exe on Windows")
def test_missing_download_module_still_resolves(monkeypatch, tmp_path) -> None:
    """An SDK without ``_cli_download`` must degrade, not fail the whole probe."""
    on_path = _executable(tmp_path / "bin" / "copilot")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    _fake_sdk(monkeypatch)

    assert runtime.runtime_binary_path() == on_path


def test_legacy_bundled_binary_is_last_resort(monkeypatch, tmp_path) -> None:
    bundled = _executable(tmp_path / "wheel" / "copilot")
    _fake_sdk(monkeypatch, cached=None)
    client_mod = sys.modules["copilot.client"]
    client_mod._get_bundled_cli_path = lambda: bundled  # type: ignore[attr-defined]

    assert runtime.runtime_binary_path() == bundled


def test_nothing_resolves_reports_actionable_detail(monkeypatch) -> None:
    _fake_sdk(monkeypatch, cached=None)

    assert runtime.runtime_binary_path() is None
    ok, detail = runtime.agents_available()
    assert ok is False
    assert "COPILOT_CLI_PATH" in detail


def test_runtime_env_pins_the_resolved_binary(monkeypatch, tmp_path) -> None:
    """The manager hands the probe's answer to the SDK over its env contract.

    Otherwise the SDK re-resolves on its own — and its last step *downloads*, so
    a PATH-only machine would pass the probe and then pull the CLI at startup.
    """
    from precursor.backend.services.agents import manager

    resolved = _executable(tmp_path / "bin" / "copilot")
    monkeypatch.setattr(runtime, "runtime_binary_path", lambda: resolved)

    env = manager._runtime_env()
    assert env["COPILOT_CLI_PATH"] == resolved
    assert env["PATH"] == os.environ["PATH"]


def test_runtime_env_omits_the_pin_when_unresolved(monkeypatch) -> None:
    from precursor.backend.services.agents import manager

    monkeypatch.setattr(runtime, "runtime_binary_path", lambda: None)

    assert "COPILOT_CLI_PATH" not in manager._runtime_env()
