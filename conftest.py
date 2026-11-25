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
import hashlib
import os
import shutil
import sqlite3
import tempfile
import uuid
import warnings
from collections.abc import Iterable, Iterator

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
# launchd/systemd address jobs by name, so the names must be throwaway as well.
_units_label = f"io.github.precursor-tests.{uuid.uuid4().hex[:12]}"
# Windows keeps its login items in the registry rather than in files, so the
# same isolation needs a throwaway key in place of the real `Run` key.
_units_run_root = r"Software\Precursor-tests"
_units_run_parent = rf"{_units_run_root}\{uuid.uuid4().hex}"
_units_run_key = rf"{_units_run_parent}\Run"


@atexit.register
def _cleanup_tmp_db() -> None:
    with contextlib.suppress(OSError):
        os.unlink(_tmp.name)
    shutil.rmtree(_skills_dir, ignore_errors=True)
    shutil.rmtree(_data_dir, ignore_errors=True)
    shutil.rmtree(_units_dir, ignore_errors=True)
    if os.name == "nt":
        import winreg

        # Innermost first: DeleteKey refuses a key that still has subkeys, which
        # also leaves the shared root alone while another session uses it.
        for key in (_units_run_key, _units_run_parent, _units_run_root):
            with contextlib.suppress(OSError):
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)


@pytest.fixture(autouse=True)
def _isolated_autostart_units(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every login-item lookup at a throwaway directory.

    ``_target_path`` is the one chokepoint every file-backed login item goes
    through, so patching it covers ``info``/``install``/``uninstall`` and — the
    dangerous ones — ``managed_unit`` and the ``launchctl``/``systemctl`` calls
    behind ``stop_unit`` and ``restart_unit``. ``_run_key`` is its Windows
    registry counterpart. Distinct paths per unit are preserved so tests can
    still tell the app and tray units apart.

    The path alone isn't enough: ``launchctl bootout gui/<uid>/<label>`` and
    ``systemctl --user stop <unit>`` address the job by *name*. A test that left
    a plist in the throwaway dir made a later stop look "controllable", and the
    real ``launchctl`` then booted out the developer's running instance. That
    happened under ``pytest -n``, which reshuffles which test runs next. So the
    names are throwaway too: an unstubbed call can only reach a job that doesn't exist.
    """
    from pathlib import Path

    from precursor.backend import autostart

    # A plist left by an earlier install test would make every later stop or
    # restart in this process think launchd owns the instance.
    shutil.rmtree(_units_dir, ignore_errors=True)
    os.makedirs(_units_dir, exist_ok=True)
    monkeypatch.setattr(autostart, "LAUNCHD_LABEL", _units_label)
    monkeypatch.setattr(autostart, "SYSTEMD_UNIT", f"{_units_label}.service")
    monkeypatch.setattr(
        autostart,
        "_target_path",
        lambda unit: Path(_units_dir) / f"{unit.label}.plist",
    )
    monkeypatch.setattr(autostart, "_run_key", lambda: _units_run_key)


@pytest.fixture(scope="session", autouse=True)
def _installs_start_in_database_mode() -> Iterator[None]:
    """The scratch DB is always "fresh", which would flip every app start to
    files mode. Tests opt in by calling ``adopt_files_if_fresh`` themselves; the
    same goes for ``backfill_role_files``, which would file the shared roles.

    Session-scoped so it is in place before any module-scoped fixture starts the
    app: function scope came too late for those, and the first module to start
    the app in a worker switched the whole worker to files mode for good.
    """
    from precursor.backend import main

    async def _skip(_session: object) -> bool:
        return False

    async def _none(_session: object) -> int:
        return 0

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main, "adopt_files_if_fresh", _skip)
        patch.setattr(main, "backfill_role_files", _none)
        yield


# Set by the process that migrated a template database, inherited by xdist workers.
_DB_TEMPLATE_ENV = "PRECURSOR_TEST_DB_TEMPLATE"


def _migrate_scratch_db() -> None:
    import asyncio

    from precursor.backend.db import engine, init_db

    async def _migrate() -> None:
        await init_db()
        # Close the pool so nothing still holds the file (Windows won't share it).
        await engine.dispose()

    asyncio.run(_migrate())


def pytest_configure(config: pytest.Config) -> None:
    """Migrate one scratch DB before xdist starts its workers.

    The ~80 migrations take 15-30 s on a Windows runner, and every worker used
    to replay them before its first test. The controller (or the only process,
    without xdist) migrates its own scratch DB instead and publishes it as the
    template that :func:`_scratch_db_at_head` copies. Workers spawn after this
    hook, so they inherit the variable.
    """
    if hasattr(config, "workerinput") or os.environ.get(_DB_TEMPLATE_ENV):
        return
    _migrate_scratch_db()
    os.environ[_DB_TEMPLATE_ENV] = _tmp.name


@pytest.fixture(scope="session", autouse=True)
def _scratch_db_at_head() -> None:
    """Bring this process's scratch DB to head once, before its first test.

    Most tests reach the schema through an app lifespan (``init_db`` runs
    ``alembic upgrade head``), but a test that seeds rows directly relied on an
    earlier test in the same process having done that. Run alone, or first in
    an xdist worker, it failed with "no such table".

    A worker starts from a copy of the template :func:`pytest_configure`
    migrated (SQLite's backup API, so pages still in the WAL come along), then
    ``init_db`` confirms it is at head, which is then a no-op.
    """
    template = os.environ.get(_DB_TEMPLATE_ENV)
    if template and os.path.exists(template) and template != _tmp.name:
        src, dst = sqlite3.connect(template), sqlite3.connect(_tmp.name)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
    _migrate_scratch_db()


@pytest.fixture(autouse=True)
def _sse_shutdown_flag_is_per_test():
    """Don't let one test's server shutdown end every later SSE stream.

    ``sse_starlette`` keeps a *process-wide* ``AppStatus.should_exit``, and its
    shutdown watcher sets it when it sees the uvicorn server it found (through
    the SIGTERM handler) asked to exit — which is exactly what a test does to
    stop an in-process server. Once set, every later ``EventSourceResponse``
    returns before sending anything ("No response returned"). Whether the
    watcher gets to look before its loop goes away is timing: on Windows it
    does, and the MCP HTTP and workspace-chat streams after it all failed.
    """
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit = False
    yield
    AppStatus.should_exit = False


def _apply_dangling_fk_actions(con: sqlite3.Connection) -> None:
    """Do what the schema's ``ON DELETE`` clauses would have done.

    SQLite only honours them with ``PRAGMA foreign_keys=ON``, which the app
    doesn't set, so a test that deletes a parent leaves its children behind.
    Without AUTOINCREMENT, SQLite then hands the freed id to the next insert,
    which inherits them: a new agent got a deleted one's events, a new topic a
    deleted one's usage-ledger rows. Repeats until stable so multi-level
    cascades settle.
    """
    tables = [
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for _ in range(5):
        changed = 0
        for table in tables:
            for fk in con.execute(f'PRAGMA foreign_key_list("{table}")').fetchall():
                parent, col, ref, on_delete = fk[2], fk[3], fk[4] or "rowid", fk[6]
                dangling = (
                    f'"{col}" IS NOT NULL AND "{col}" NOT IN (SELECT "{ref}" FROM "{parent}")'
                )
                if on_delete == "CASCADE":
                    cur = con.execute(f'DELETE FROM "{table}" WHERE {dangling}')
                elif on_delete == "SET NULL":
                    cur = con.execute(f'UPDATE "{table}" SET "{col}" = NULL WHERE {dangling}')
                else:
                    continue
                changed += cur.rowcount
        if not changed:
            return


@pytest.fixture(autouse=True)
def _shared_db_stays_consistent() -> Iterator[None]:
    """Leave the shared scratch DB as a well-behaved app would after each test.

    Every test in a process shares one SQLite file, so whatever a test leaves
    behind becomes the next test's surprise, and ``pytest -n`` reshuffles which
    tests come next. Two leftovers bit for real:

    * **Enabled schedules.** Each ``TestClient(create_app())`` starts the real
      scheduler, which runs any due topic/agent schedule or workflow it finds.
      "Run now" leaves a row due and leased; once the lease lapses, a later
      app reclaims it and drives a full turn through *that* test's patched
      provider (the workspace chat round-cap test counted the rounds as its own).
    * **Orphaned children** of deleted rows (see :func:`_apply_dangling_fk_actions`).
    """
    yield
    con = sqlite3.connect(_tmp.name, timeout=10)
    try:
        for statement in (
            "UPDATE topic_schedule SET enabled = 0 WHERE enabled = 1",
            "UPDATE agent_schedule SET enabled = 0 WHERE enabled = 1",
            "UPDATE workflows SET schedule_enabled = 0 WHERE schedule_enabled = 1",
        ):
            try:
                con.execute(statement)
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):  # not migrated yet is fine
                    raise
        _apply_dangling_fk_actions(con)
        con.commit()
    except sqlite3.OperationalError as exc:
        # Housekeeping, not an assertion: a lock some app task left behind belongs
        # to the test that caused it, not to this teardown.
        con.rollback()
        warnings.warn(f"shared-DB cleanup skipped: {exc}", stacklevel=1)
    finally:
        con.close()


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
    CLI the ``microsoft-copilot-sdk`` package drives whenever that package
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
def _no_docker_probe():
    """Answer the Docker capability probe without running ``docker version``.

    ``GET /api/settings`` reports whether Docker is usable, and the suite reads
    settings a few hundred times. Each read shelled out to the real CLI, which
    is installed on CI runners and is slowest exactly where subprocesses
    already are: on Windows. Tests about the cmd-runner's jail patch this same
    attribute with the answer they need.
    """
    from precursor.backend.services import cmd_runner

    prev = cmd_runner.docker_available
    cmd_runner.docker_available = lambda: (False, "test: docker probe stubbed out")
    yield
    cmd_runner.docker_available = prev


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


def shard_selection(keys: Iterable[str], shard: int, total: int) -> list[bool]:
    """Whether each test key belongs to ``shard`` (1-based) of ``total``.

    A content hash, not ``hash()``: string hashing is salted per process, and
    every xdist worker must deselect exactly the same tests as the controller.
    """
    return [
        int.from_bytes(hashlib.sha1(key.encode()).digest()[:8], "big") % total == shard - 1
        for key in keys
    ]


def _shard_key(item: pytest.Item) -> str:
    # Tests pinned together for xdist share a resource, so they share a shard too.
    marker = item.get_closest_marker("xdist_group")
    if marker is not None:
        name = marker.args[0] if marker.args else marker.kwargs.get("name", "")
        return f"xdist_group:{name}"
    return item.nodeid


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Run one slice of the suite when ``PRECURSOR_TEST_SHARD=k/n`` is set.

    CI splits the backend suite across parallel jobs this way, so the slowest
    platform no longer gates every merge on the whole suite in one runner.
    Unset, every test runs.
    """
    spec = os.environ.get("PRECURSOR_TEST_SHARD", "").strip()
    if not spec:
        return
    shard_text, _, total_text = spec.partition("/")
    shard, total = int(shard_text), int(total_text)
    if not 1 <= shard <= total:
        raise pytest.UsageError(f"PRECURSOR_TEST_SHARD={spec!r}: expected k/n with 1 <= k <= n")
    keep = shard_selection([_shard_key(item) for item in items], shard, total)
    config.hook.pytest_deselected(items=[i for i, k in zip(items, keep, strict=True) if not k])
    items[:] = [i for i, k in zip(items, keep, strict=True) if k]
