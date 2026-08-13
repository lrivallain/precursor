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


@atexit.register
def _cleanup_tmp_db() -> None:
    with contextlib.suppress(OSError):
        os.unlink(_tmp.name)
    shutil.rmtree(_skills_dir, ignore_errors=True)
    shutil.rmtree(_data_dir, ignore_errors=True)


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
