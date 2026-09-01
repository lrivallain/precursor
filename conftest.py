"""Pytest fixtures + global test isolation.

CRITICAL: point the app at a throwaway SQLite DB *before* anything imports the
backend. ``db.py`` builds its engine at import time from
``get_settings().database_url`` (which otherwise reads ``.env`` → the real
``./precursor.db``), so tests that write settings would pollute the dev
database. Setting the env var here — the first thing pytest loads — guarantees
the cached settings pick up the temp DB instead.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import shutil
import tempfile

import pytest

_tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115 - kept on disk for the whole test session
    prefix="precursor-test-", suffix=".db", delete=False
)
_tmp.close()
os.environ["PRECURSOR_DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp.name}"

# Isolate skills so creating/migrating a skill writes SKILL.md files into a
# throwaway directory instead of the developer's real ``~/.copilot/skills``.
_skills_dir = tempfile.mkdtemp(prefix="precursor-test-skills-")
os.environ["PRECURSOR_SKILLS_DIR"] = _skills_dir

# Isolate the on-disk data directory (attachment blobs, workspaces, …) so tests
# write content-addressed attachment files into a throwaway dir instead of the
# developer's real ``./.precursor``.
_data_dir = tempfile.mkdtemp(prefix="precursor-test-data-")
os.environ["PRECURSOR_DATA_DIR"] = _data_dir

# Keep the startup MCP warm-up out of the suite. Every ``TestClient(create_app())``
# runs the real lifespan, and a sweep that outlives the client's 5s grace period
# would spawn ``npx`` subprocesses for the stdio built-ins on a developer machine
# where they happen to be enabled. Tests that exercise the warm-up build their own
# ``MCPWarmUp`` with explicit settings.
os.environ["PRECURSOR_MCP_WARMUP_ENABLED"] = "false"

# Isolate the login-item units. Unlike everything above, these are NOT addressed
# by an env var: launchd reads ``~/Library/LaunchAgents`` and systemd
# ``~/.config/systemd/user``, both of which are global to the user account.
#
# That mattered more than it sounds. ``supervisor.stop()`` and ``restart()`` ask
# ``managed_unit()`` whether a *controllable* login item owns this instance, and
# it answered by looking at the developer's real plist — so a supervisor test
# with a perfectly isolated data dir would still `launchctl bootout` the
# developer's actual running Precursor. Booted out, it does not come back:
# KeepAlive can only restart a job that is still loaded. Running the test suite
# killed the machine's real instance.
_units_dir = tempfile.mkdtemp(prefix="precursor-test-units-")


@atexit.register
def _cleanup_tmp_db() -> None:
    with contextlib.suppress(OSError):
        os.unlink(_tmp.name)
    shutil.rmtree(_skills_dir, ignore_errors=True)
    shutil.rmtree(_data_dir, ignore_errors=True)
    shutil.rmtree(_units_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolated_autostart_units(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every login-item lookup at a throwaway directory.

    ``_target_path`` is the one chokepoint all three platforms go through, so
    patching it covers ``info``/``install``/``uninstall`` and — the dangerous
    ones — ``managed_unit`` and the ``launchctl``/``systemctl`` calls behind
    ``stop_unit`` and ``restart_unit``. Distinct paths per unit are preserved so
    tests can still tell the app and tray units apart.
    """
    from pathlib import Path

    from precursor.backend import autostart

    monkeypatch.setattr(
        autostart,
        "_target_path",
        lambda unit: Path(_units_dir) / f"{unit.label}.plist",
    )


@pytest.fixture(autouse=True)
def _clean_skills_dir() -> None:
    """Empty the throwaway skills dir before each test for isolation."""
    shutil.rmtree(_skills_dir, ignore_errors=True)
    os.makedirs(_skills_dir, exist_ok=True)


@pytest.fixture(autouse=True)
def _stub_playwright_browser_probe():
    """Keep the Playwright ``--browser`` capability probe off the network.

    ``configure_playwright_server`` (run on every app startup) shells out to
    ``npx @playwright/mcp --help`` to learn whether the resolved build accepts
    ``--browser``. Priming the module cache to ``True`` short-circuits that probe
    so tests never spawn ``npx`` and the SSO-friendly ``msedge`` default the
    built-in tests assert is preserved. Tests exercising the probe itself reset
    the cache to ``None`` explicitly.
    """
    from precursor.backend.services.mcp import client as mcp_client

    prev = mcp_client._playwright_browser_flag_support
    mcp_client._playwright_browser_flag_support = True
    yield
    mcp_client._playwright_browser_flag_support = prev


@pytest.fixture(autouse=True)
def _no_agents_runtime():
    """Keep app startup from spawning the real Copilot CLI child process.

    ``lifespan`` calls ``AgentManager.start()``, which spawns the native Copilot
    CLI the optional ``github-copilot-sdk`` package drives whenever that package
    is importable *and* the persisted ``agents_enabled`` flag is on. Both hold in
    a developer venv that ever ran ``make dev``: exercising Agents mode means
    turning that flag on (~40 call sites across the suite), and it is written to
    the session-wide scratch DB, so those tests — and every later app startup
    while it stays on — paid a real process spawn and teardown. At ~12.8s each
    that took the suite from ~75s to ~9min, on machines with the extra installed
    only, so it silently punished exactly the people running the app.

    Reporting the runtime as unavailable is the capability seam the manager
    already consults, so the suite behaves identically with or without the extra
    installed. Tests that want the runtime live monkeypatch this same attribute
    back (see ``test_agents.py``), which still wins over this fixture.
    """
    from precursor.backend.services.agents import runtime

    prev = runtime.agents_available
    runtime.agents_available = lambda: (False, "test: agents runtime stubbed out")  # type: ignore[assignment]
    yield
    runtime.agents_available = prev  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _no_llm_credentials():
    """Keep the LLM provider off the network by pretending there's no token.

    ``get_llm_provider`` falls back to ``MockProvider`` when no GitHub token
    resolves, and every test that doesn't inject its own fake provider was
    silently relying on that — which only holds while the developer is signed
    out of ``gh``. Signed in, ``resolve_github_token`` finds a real credential,
    the tests issue live GitHub Models requests, and they fail against an
    entitlement the token doesn't have (or, worse, pass slowly while billing
    someone).

    Only the name bound inside ``services.llm`` is replaced, so the GitHub
    *data* paths (issues, projects, stats) keep their own resolution and the
    tests that stub it there are untouched. A test that wants the real
    selection logic can patch this attribute back.
    """
    from precursor.backend.services import llm as llm_module

    async def _no_token(_session):  # type: ignore[no-untyped-def]
        return ""

    prev = llm_module.resolve_github_token
    llm_module.resolve_github_token = _no_token  # type: ignore[assignment]
    yield
    llm_module.resolve_github_token = prev  # type: ignore[assignment]
