"""Provisioning the Copilot CLI from Settings — the seam that unblocks Agents mode.

Agents mode used to be the one capability Precursor could not turn on itself.
The SDK is a normal dependency now, so the only piece that can be missing is the
native CLI it drives — and these tests pin the contract the panel relies on to
install it: the runtime endpoint reports *why* it's unavailable and *what can be
done*, the download runs as a job rather than a blocking request, and neither
route hides behind the very runtime it exists to provision.

The download itself is always faked. Actually fetching ~90 MB from GitHub is not
a unit test, and the point here is the surrounding machinery.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import tomllib
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from precursor.backend import supervisor
from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AgentEventRecord, AgentSession
from precursor.backend.services.agents import provision, runtime


@pytest.fixture(autouse=True)
def _clean_job():
    provision.reset()
    yield
    provision.reset()


def _fake_download_module(monkeypatch, result="/tmp/copilot", error: Exception | None = None):
    """Install a ``copilot._cli_download`` whose downloader we control."""
    module = types.ModuleType("copilot._cli_download")

    def get_or_download_cli(version=None):
        if error is not None:
            raise error
        return result

    module.get_or_download_cli = get_or_download_cli  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "copilot._cli_download", module)
    return module


async def _clear_agent_history() -> None:
    """Empty the agent tables — the temp DB is shared for the whole session."""
    async with SessionLocal() as session:
        await session.execute(delete(AgentEventRecord))
        await session.execute(delete(AgentSession))
        await session.commit()


async def _settle(job: provision.ProvisionJob) -> None:
    """Wait for the background task to finish, without sleeping blindly."""
    for _ in range(200):
        if job.state != "running":
            return
        await asyncio.sleep(0.01)
    raise AssertionError("provisioning job never settled")


# --- the capability report -------------------------------------------------


def test_runtime_endpoint_reports_how_to_fix_an_unavailable_runtime(monkeypatch) -> None:
    """The whole point of the endpoint: not just "off", but "off, and here's the fix"."""
    monkeypatch.setattr(runtime, "agents_available", lambda: (False, "no CLI"))
    monkeypatch.setattr(runtime, "runtime_binary_path", lambda: None)
    monkeypatch.setattr(provision, "download_supported", lambda: (True, "ready"))

    app = create_app()
    with TestClient(app) as client:
        body = client.get("/api/agents/runtime").json()

    assert body["available"] is False
    assert body["unavailable_reason"] == "no CLI"
    assert body["can_install_cli"] is True
    assert body["job"] is None


def test_runtime_routes_are_reachable_while_agents_are_disabled() -> None:
    """They must not be gated on the runtime they exist to install.

    Every other route in the namespace is refused with 409 when Agents mode is
    off (see ``test_agents_disabled_by_default``). These three are the exception,
    and it is the exception that makes the feature recoverable from the UI.
    """
    app = create_app()
    with TestClient(app) as client:
        assert client.post("/api/agents", json={"task": "x"}).status_code == 409
        assert client.get("/api/agents/runtime").status_code == 200


def test_a_fresh_install_reports_no_archived_timeline() -> None:
    """Drives whether the retention levers are worth showing with Agents off.

    The sweep is gated on ``scheduler_enabled``, not on Agents mode, so it keeps
    pruning after the feature is switched off — which is why the levers stay
    reachable while events exist. With none on disk there is nothing for them to
    protect, and the panel drops the section instead of explaining a background
    job that has no work to do.
    """
    app = create_app()
    with TestClient(app) as client:
        # Isolate from other tests sharing the session-wide temp DB.
        asyncio.run(_clear_agent_history())
        assert client.get("/api/agents/runtime").json()["has_archived_events"] is False


def test_an_archived_timeline_keeps_the_retention_levers_reachable() -> None:
    """The dangerous combination this guards against.

    The sweep keeps pruning ``agent_events`` after Agents mode is switched off,
    so hiding the levers while history exists would leave no way to stop a
    background job quietly erasing it. Once there are events, the flag is set
    regardless of whether the feature is on.
    """

    async def seed() -> None:
        async with SessionLocal() as session:
            agent = AgentSession(title="Old run", task_prompt="x", status="completed")
            session.add(agent)
            await session.flush()
            session.add(AgentEventRecord(agent_session_id=agent.id, payload='{"kind":"archived"}'))
            await session.commit()

    app = create_app()
    with TestClient(app) as client:
        asyncio.run(seed())
        try:
            assert client.get("/api/agents/runtime").json()["has_archived_events"] is True
        finally:
            # The temp DB is shared for the whole session.
            asyncio.run(_clear_agent_history())


def test_old_sdk_line_reports_that_there_is_nothing_to_download(monkeypatch) -> None:
    """Wheels before 1.0.4 bundled the CLI and expose no downloader.

    Our floor excludes that line, but a shared environment can still produce
    one — so the panel must be told to hide the button rather than offer an
    action that raises.
    """
    monkeypatch.setitem(sys.modules, "copilot._cli_download", None)
    monkeypatch.setattr(runtime, "sdk_installed", lambda: True)

    ok, detail = provision.download_supported()
    assert ok is False
    assert "nothing to install" in detail


def test_download_unsupported_without_the_sdk(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "sdk_installed", lambda: False)

    ok, detail = provision.download_supported()
    assert ok is False
    assert "github-copilot-sdk" in detail


# --- the job ---------------------------------------------------------------


def test_successful_download_starts_the_runtime_without_a_restart(monkeypatch) -> None:
    """The common case ends with a working runtime and no second step."""
    _fake_download_module(monkeypatch, result="/tmp/copilot-cli")
    monkeypatch.setattr(runtime, "sdk_installed", lambda: True)

    started = {"called": False}

    class _Manager:
        ready = True

        async def start(self) -> None:
            started["called"] = True

    manager_mod = importlib.import_module("precursor.backend.services.agents.manager")
    monkeypatch.setattr(manager_mod, "get_agent_manager", lambda: _Manager())

    async def run() -> provision.ProvisionJob:
        job = provision.start_download()
        await _settle(job)
        return job

    job = asyncio.run(run())

    assert job.state == "succeeded"
    assert job.cli_path == "/tmp/copilot-cli"
    assert started["called"] is True
    assert job.runtime_started is True
    assert "ready" in job.detail


def test_download_that_cannot_start_the_runtime_asks_for_a_restart(monkeypatch) -> None:
    """The CLI is on disk either way — a failed start is not a failed install."""
    _fake_download_module(monkeypatch, result="/tmp/copilot-cli")
    monkeypatch.setattr(runtime, "sdk_installed", lambda: True)

    class _Manager:
        ready = False

        async def start(self) -> None:
            raise RuntimeError("client would not launch")

    manager_mod = importlib.import_module("precursor.backend.services.agents.manager")
    monkeypatch.setattr(manager_mod, "get_agent_manager", lambda: _Manager())

    async def run() -> provision.ProvisionJob:
        job = provision.start_download()
        await _settle(job)
        return job

    job = asyncio.run(run())

    assert job.state == "succeeded"
    assert job.runtime_started is False
    assert "Restart" in job.detail


def test_failed_download_surfaces_the_sdk_error(monkeypatch) -> None:
    """A generic "unavailable" is what made this unfixable in the first place."""
    _fake_download_module(monkeypatch, error=OSError("connection reset by peer"))
    monkeypatch.setattr(runtime, "sdk_installed", lambda: True)

    async def run() -> provision.ProvisionJob:
        job = provision.start_download()
        await _settle(job)
        return job

    job = asyncio.run(run())

    assert job.state == "failed"
    assert job.error is not None
    assert "connection reset by peer" in job.error


def test_starting_twice_reuses_the_running_job(monkeypatch) -> None:
    """A double-clicked button must not fetch the same 90 MB twice."""
    calls = {"n": 0}
    module = types.ModuleType("copilot._cli_download")

    def get_or_download_cli(version=None):
        calls["n"] += 1
        return "/tmp/copilot-cli"

    module.get_or_download_cli = get_or_download_cli  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "copilot._cli_download", module)
    monkeypatch.setattr(runtime, "sdk_installed", lambda: True)

    class _Manager:
        ready = True

        async def start(self) -> None:
            return None

    manager_mod = importlib.import_module("precursor.backend.services.agents.manager")
    monkeypatch.setattr(manager_mod, "get_agent_manager", lambda: _Manager())

    async def run() -> None:
        first = provision.start_download()
        assert provision.start_download() is first
        await _settle(first)

    asyncio.run(run())
    assert calls["n"] == 1


def test_unsupported_download_records_a_failed_job_rather_than_raising(monkeypatch) -> None:
    monkeypatch.setattr(provision, "download_supported", lambda: (False, "no downloader here"))

    app = create_app()
    with TestClient(app) as client:
        body = client.post("/api/agents/runtime/cli").json()

    assert body["job"]["state"] == "failed"
    assert body["job"]["error"] == "no downloader here"


# --- the restart hand-off --------------------------------------------------


def test_restart_is_refused_when_the_instance_is_not_supervised(monkeypatch) -> None:
    """A ``uv run precursor --dev`` stack publishes no runtime state.

    Better a 409 the panel can explain than a button that appears to work.
    """
    monkeypatch.setattr(supervisor, "_read_state", lambda: None)

    app = create_app()
    with TestClient(app) as client:
        response = client.post("/api/agents/runtime/restart")

    assert response.status_code == 409
    assert "supervised" in response.json()["detail"]


def test_capability_probe_never_evicts_the_runtime_state_file(monkeypatch) -> None:
    """The panel polls ``GET /runtime``; a poll must not mutate supervisor state.

    ``supervisor.status()`` *deletes* ``runtime.json`` when it judges the
    recorded pid dead. Reaching for it here would mean a probe — served on a
    1.5s timer while a download runs — could evict a live instance's own
    bookkeeping on a single misread, leaving `service status` and the tray
    reporting "not running" for a server that is plainly serving.
    """
    monkeypatch.setattr(
        supervisor, "status", lambda: pytest.fail("the probe must not call status()")
    )
    monkeypatch.setattr(supervisor, "_read_state", lambda: None)

    app = create_app()
    with TestClient(app) as client:
        body = client.get("/api/agents/runtime").json()

    assert body["can_restart"] is False


def test_restart_hands_off_to_a_detached_child(monkeypatch) -> None:
    """The child is about to kill us, so it must not be in our process group."""
    monkeypatch.setattr(supervisor, "restartable", lambda: (True, "ready"))
    seen: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(supervisor.subprocess, "Popen", fake_popen)

    app = create_app()
    with TestClient(app) as client:
        assert client.post("/api/agents/runtime/restart").status_code == 202

    assert seen["cmd"][1:] == ["-m", "precursor.backend", "service", "restart"]
    kwargs = seen["kwargs"]
    assert kwargs.get("start_new_session") or kwargs.get("creationflags")


# --- the dependency that makes all of this possible ------------------------


def test_sdk_is_a_default_dependency_floored_above_the_bundled_wheels() -> None:
    """Guards the reason Agents mode can ship on by default.

    ``github-copilot-sdk`` up to 1.0.2 published six platform-specific wheels
    that each bundled the native CLI (~145 MB unpacked); 1.0.4 replaced them with
    a single ~0.5 MB pure-Python wheel that downloads it on demand. Only the
    latter is defensible in a default install, and the difference is invisible
    in a diff — hence a test.
    """
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as handle:
        manifest = tomllib.load(handle)

    requirements = manifest["project"]["dependencies"]
    sdk = [r for r in requirements if r.startswith("github-copilot-sdk")]
    assert sdk, "the Copilot SDK must be a default dependency, not an extra"
    assert ">=1.0.4" in sdk[0], "a lower floor would resolve a wheel bundling ~145 MB"
    assert "<2" in sdk[0], "we depend on private SDK internals; keep the major cap"

    # The retired extra stays declared but empty, so an install that still names
    # it — including one rebuilt from uv's receipt — keeps resolving.
    assert manifest["project"]["optional-dependencies"]["agents"] == []
