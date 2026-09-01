"""Provision the native Copilot CLI that the Agents runtime drives.

The Copilot SDK is a normal dependency, so the Python side of Agents mode is
always present. What can be missing is the **native CLI** (~90 MB) that the SDK
downloads on demand — and that is the one thing standing between a fresh install
and a working Agents mode.

:mod:`.runtime` deliberately never triggers that download: it is a probe, and it
runs on every Settings render. This module is the other half — the explicit,
user-initiated action that *does* fetch it, so the panel can offer a button
instead of a command to go type somewhere else.

Two shapes to accommodate:

* **A download outlives its request.** ~90 MB over a slow link takes minutes, so
  the HTTP handler starts a job and returns; the panel polls the runtime status,
  which carries the job. One job at a time — there is nothing to reconcile
  between two concurrent downloads of the same file.
* **The old SDK line has nothing to download.** Wheels before 1.0.4 bundled the
  binary and expose no ``_cli_download`` module. Our floor excludes them, but a
  shared environment can still produce one, so the step reports why it is a
  no-op rather than raising.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from starlette.concurrency import run_in_threadpool

from precursor.backend.services.agents import runtime

logger = logging.getLogger(__name__)

JobState = Literal["running", "succeeded", "failed"]

_DOWNLOAD_MODULE = "copilot._cli_download"


@dataclass
class ProvisionJob:
    """The one in-flight (or last finished) CLI provisioning attempt."""

    state: JobState = "running"
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    # What we are doing / did, in the panel's words.
    detail: str = "Downloading the Copilot CLI…"
    # The failure as the SDK reported it. The issue's explicit ask: a real error,
    # not a generic "unavailable".
    error: str | None = None
    cli_path: str | None = None
    # True when the runtime came up in this process, so the panel knows it does
    # not need to ask for a restart.
    runtime_started: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "error": self.error,
            "cli_path": self.cli_path,
            "runtime_started": self.runtime_started,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


_job: ProvisionJob | None = None
_task: asyncio.Task[None] | None = None


def current_job() -> ProvisionJob | None:
    return _job


def job_running() -> bool:
    return _job is not None and _job.state == "running"


def download_supported() -> tuple[bool, str]:
    """Whether this SDK can fetch a CLI for us, and why not when it can't."""
    if not runtime.sdk_installed():
        return False, "github-copilot-sdk is missing from this installation."
    try:
        module = importlib.import_module(_DOWNLOAD_MODULE)
    except Exception:  # pragma: no cover - only on the pre-1.0.4 bundled line
        return False, (
            "This Copilot SDK build bundles its own CLI instead of downloading "
            "one, so there is nothing to install."
        )
    if not hasattr(module, "get_or_download_cli"):  # pragma: no cover - defensive
        return False, "This Copilot SDK build exposes no CLI download helper."
    return True, "ready"


def _download() -> str:
    """Fetch (or reuse) the CLI the SDK expects. Blocking — run off the loop."""
    module = importlib.import_module(_DOWNLOAD_MODULE)
    path = module.get_or_download_cli()
    if not path:
        raise RuntimeError("The Copilot SDK returned no CLI path after downloading.")
    return str(path)


async def _run_job(job: ProvisionJob) -> None:
    try:
        job.cli_path = await run_in_threadpool(_download)
    except Exception as exc:
        logger.exception("Copilot CLI provisioning failed")
        job.state = "failed"
        job.error = f"{type(exc).__name__}: {exc}".strip()
        job.detail = "Could not install the Copilot CLI."
        job.finished_at = time.time()
        return

    # The probe caches nothing itself, but `find_spec` consults the import
    # system's directory cache — and we just wrote into one of those directories.
    importlib.invalidate_caches()

    # Bring the runtime up in *this* process so the common case needs no restart.
    # A failure here is not a failed provision: the CLI is on disk either way,
    # and the panel falls back to offering a restart.
    try:
        from precursor.backend.services.agents.manager import get_agent_manager

        await get_agent_manager().start()
        job.runtime_started = get_agent_manager().ready
    except Exception:
        logger.exception("Agents runtime failed to start after provisioning the CLI")
        job.runtime_started = False

    job.state = "succeeded"
    job.detail = (
        "Copilot CLI installed — Agents mode is ready."
        if job.runtime_started
        else "Copilot CLI installed. Restart Precursor to start the runtime."
    )
    job.finished_at = time.time()


def start_download() -> ProvisionJob:
    """Kick off a CLI download, or return the one already running.

    Idempotent on purpose: a double-clicked button must not start a second
    download of the same file.
    """
    global _job, _task
    if _job is not None and _job.state == "running":
        return _job

    ok, detail = download_supported()
    if not ok:
        _job = ProvisionJob(
            state="failed",
            detail="Cannot install the Copilot CLI from here.",
            error=detail,
            finished_at=time.time(),
        )
        return _job

    _job = ProvisionJob()
    _task = asyncio.create_task(_run_job(_job))
    return _job


def reset() -> None:
    """Drop the recorded job (tests, and dismissing a finished result)."""
    global _job, _task
    _job = None
    _task = None
