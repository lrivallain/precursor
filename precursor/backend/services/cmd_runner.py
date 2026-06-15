"""Sandboxed local command execution for the cmd-runner MCP server.

Two modes, selected by ``settings.cmd_runner_jail``:

* **jail (default)** — each command runs inside a throwaway Docker container
  (``docker run --rm``) with the working directory bind-mounted at ``/work``.
  Network is off by default and CPU / memory / pid limits are applied. On POSIX
  the container runs as the host user so files written into a mounted workspace
  aren't root-owned. Requires the ``docker`` CLI plus a running daemon.
* **host (jail disabled)** — commands run directly on the host with the same
  privileges as the backend process. This grants **full local disk access** and
  is only intended for trusted, single-user setups. No Docker needed.

The jail toggle is enforced at enable time via :func:`jail_preflight_error`
(the MCP server refuses to enable when jail mode is on but Docker is
unavailable) and again at run time as defence in depth.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from precursor.backend.config import Settings

logger = logging.getLogger(__name__)

# How long to wait when probing the docker daemon for availability.
_DOCKER_PROBE_TIMEOUT = 5.0


@dataclass(slots=True)
class CmdRunnerConfig:
    """Effective command-runner settings (config defaults + DB overrides)."""

    jail: bool
    image: str
    network: bool
    timeout_seconds: int
    max_output_bytes: int
    memory: str
    pids_limit: int
    cpus: str

    @classmethod
    def from_settings(cls, settings: Settings) -> CmdRunnerConfig:
        """Build from env-based config (the factory defaults, no DB overrides)."""
        return cls(
            jail=settings.cmd_runner_jail,
            image=settings.cmd_runner_image,
            network=settings.cmd_runner_network,
            timeout_seconds=settings.cmd_runner_timeout_seconds,
            max_output_bytes=settings.cmd_runner_max_output_bytes,
            memory=settings.cmd_runner_memory,
            pids_limit=settings.cmd_runner_pids_limit,
            cpus=settings.cmd_runner_cpus,
        )


@dataclass(slots=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    jailed: bool
    workdir: str
    truncated: bool


def docker_available() -> tuple[bool, str]:
    """Return ``(ok, detail)`` — whether the docker CLI + daemon are usable."""
    if shutil.which("docker") is None:
        return False, "docker CLI not found on PATH"
    try:
        proc = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=_DOCKER_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"docker is not runnable: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or "docker daemon is not responding").strip()
    return True, (proc.stdout or "").strip() or "ok"


def jail_preflight_error(jail_enabled: bool) -> str | None:
    """Reason the cmd-runner cannot be enabled, or ``None`` if it can.

    Only blocks when jail mode is on and Docker is unavailable. When jail mode
    is off there is no dependency — commands run directly on the host.
    """
    if not jail_enabled:
        return None
    ok, detail = docker_available()
    if ok:
        return None
    return (
        "Docker is required to run the command runner in jail mode, but it is "
        f"unavailable ({detail}). Start or install Docker, or disable jail mode "
        "(in Settings → System, or PRECURSOR_CMD_RUNNER_JAIL=false) to run "
        "commands directly on the host — note that grants full local disk access."
    )


def _truncate(data: bytes, limit: int) -> tuple[str, bool]:
    truncated = len(data) > limit
    if truncated:
        data = data[:limit]
    return data.decode("utf-8", "replace"), truncated


def _docker_argv(
    *, config: CmdRunnerConfig, workdir: Path, name: str, interactive: bool, inner: list[str]
) -> list[str]:
    argv = ["docker", "run", "--rm", "--name", name]
    if interactive:
        argv.append("-i")
    argv += ["--network", "bridge" if config.network else "none"]
    if config.memory:
        argv += ["--memory", config.memory]
    if config.pids_limit:
        argv += ["--pids-limit", str(config.pids_limit)]
    if config.cpus:
        argv += ["--cpus", str(config.cpus)]
    # Map to the host user so files created in the mounted workspace aren't
    # owned by root. Only meaningful on POSIX.
    if os.name == "posix":
        argv += ["--user", f"{os.getuid()}:{os.getgid()}"]
    argv += ["-v", f"{workdir}:/work", "-w", "/work", config.image, *inner]
    return argv


async def _reap(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(proc.wait(), timeout=5.0)


async def _docker_kill(name: str) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "kill",
            name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except Exception:  # best effort — the container will also exit on its own
        logger.debug("docker kill %s failed", name, exc_info=True)


async def run_in_sandbox(
    *,
    inner_argv: list[str],
    workdir: str | Path,
    config: CmdRunnerConfig,
    timeout: int | None = None,
    stdin_data: str | None = None,
) -> CommandResult:
    """Run ``inner_argv`` in the configured sandbox and capture its output.

    ``inner_argv`` is the command as it should run *inside* the sandbox (e.g.
    ``["bash", "-lc", cmd]`` or ``["python", "-"]`` with the program on stdin).
    """
    work = Path(workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    timeout = timeout or config.timeout_seconds
    limit = config.max_output_bytes
    jailed = config.jail
    interactive = stdin_data is not None

    container_name: str | None = None
    if jailed:
        container_name = f"precursor-cmd-{uuid.uuid4().hex[:12]}"
        argv = _docker_argv(
            config=config,
            workdir=work,
            name=container_name,
            interactive=interactive,
            inner=inner_argv,
        )
        proc_cwd: str | None = None
    else:
        argv = list(inner_argv)
        proc_cwd = str(work)

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=proc_cwd,
        stdin=asyncio.subprocess.PIPE if interactive else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdin_bytes = stdin_data.encode("utf-8") if stdin_data is not None else None
    timed_out = False
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes), timeout=timeout
        )
    except TimeoutError:
        timed_out = True
        proc.kill()
        if container_name is not None:
            await _docker_kill(container_name)
        await _reap(proc)
        stdout_b, stderr_b = b"", b""

    stdout, t1 = _truncate(stdout_b or b"", limit)
    stderr, t2 = _truncate(stderr_b or b"", limit)
    return CommandResult(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        jailed=jailed,
        workdir=str(work),
        truncated=t1 or t2,
    )
