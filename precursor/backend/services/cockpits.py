"""CockpitManager — spawns and supervises user-registered local webapps.

A *cockpit* is an arbitrary local command that starts a web server on a known
loopback port. The manager owns the **runtime** side of the feature:

* ``start`` spawns the command in its own process group (so the whole child
  tree is killable), streams its output into a bounded ring buffer, and polls
  the declared port until it accepts TCP connections (readiness).
* ``stop`` terminates the process group (SIGTERM → SIGKILL grace) on POSIX, or
  ``taskkill /T`` on Windows.
* ``stop_all`` is called from the FastAPI lifespan so nothing is orphaned when
  the backend exits.

State is intentionally in-memory only: a spawned process can never outlive the
backend, so there is nothing durable to persist. The registry is keyed by the
cockpit's DB id.

Commands run directly on the host with the backend's privileges — the same
trust model as the cmd-runner's host mode — so this is only ever wired up on a
loopback bind (enforced by the router).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
from collections import deque
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime

from precursor.backend.config import get_settings
from precursor.backend.schemas.cockpit import CockpitState, CockpitStatus

logger = logging.getLogger(__name__)

_READY_PROBE_INTERVAL = 0.25
_READY_PROBE_CONNECT_TIMEOUT = 1.0
_LOOPBACK = "127.0.0.1"


@dataclass
class _Running:
    """Live state for one spawned cockpit process."""

    port: int
    proc: asyncio.subprocess.Process
    state: CockpitState = "starting"
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    exit_code: int | None = None
    detail: str | None = None
    # Bounded ring of raw output bytes (stdout + stderr merged).
    log: deque[bytes] = field(default_factory=deque)
    log_bytes: int = 0
    # Set when the user asked us to stop, so the monitor doesn't report the
    # resulting exit as a crash.
    stopping: bool = False
    tasks: set[asyncio.Task[None]] = field(default_factory=set)

    def to_status(self) -> CockpitStatus:
        return CockpitStatus(
            state=self.state,
            pid=self.proc.pid if self.proc.returncode is None else None,
            port=self.port,
            started_at=self.started_at,
            exit_code=self.exit_code,
            detail=self.detail,
        )

    def append_log(self, chunk: bytes, limit: int) -> None:
        self.log.append(chunk)
        self.log_bytes += len(chunk)
        while self.log_bytes > limit and len(self.log) > 1:
            dropped = self.log.popleft()
            self.log_bytes -= len(dropped)

    def read_log(self) -> str:
        return b"".join(self.log).decode("utf-8", "replace")


class CockpitManager:
    """Process registry for running cockpits (one per app lifetime)."""

    def __init__(self) -> None:
        self._running: dict[int, _Running] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock(self, cockpit_id: int) -> asyncio.Lock:
        lock = self._locks.get(cockpit_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[cockpit_id] = lock
        return lock

    # ---- queries -------------------------------------------------------

    def get_status(self, cockpit_id: int) -> CockpitStatus:
        run = self._running.get(cockpit_id)
        return run.to_status() if run is not None else CockpitStatus(state="stopped")

    def is_running(self, cockpit_id: int) -> bool:
        run = self._running.get(cockpit_id)
        return run is not None and run.state in ("starting", "running")

    def running_port(self, cockpit_id: int) -> int | None:
        """Port to proxy to — only while the process is alive."""
        run = self._running.get(cockpit_id)
        if run is None or run.proc.returncode is not None:
            return None
        return run.port

    def get_logs(self, cockpit_id: int) -> str:
        run = self._running.get(cockpit_id)
        return run.read_log() if run is not None else ""

    # ---- lifecycle -----------------------------------------------------

    async def start(
        self,
        *,
        cockpit_id: int,
        command: str,
        port: int,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> CockpitStatus:
        """Spawn the cockpit if it isn't already active.

        Idempotent: if the process is already starting/running this returns its
        current status without respawning. Use :meth:`restart` to force a fresh
        spawn.
        """
        async with self._lock(cockpit_id):
            existing = self._running.get(cockpit_id)
            if existing is not None and existing.proc.returncode is None:
                return existing.to_status()
            # Clear a previous terminal record before respawning.
            self._running.pop(cockpit_id, None)

            proc_env = {**os.environ, **(env or {})}
            workdir = cwd or None
            if workdir is not None and not os.path.isdir(workdir):
                return CockpitStatus(
                    state="crashed",
                    port=port,
                    detail=f"working directory does not exist: {workdir}",
                )

            try:
                proc = await self._spawn(command, workdir, proc_env)
            except OSError as exc:
                logger.warning("Cockpit %s failed to spawn: %s", cockpit_id, exc)
                return CockpitStatus(state="crashed", port=port, detail=str(exc))

            run = _Running(port=port, proc=proc)
            self._running[cockpit_id] = run
            self._spawn_task(run, self._pump_output(cockpit_id, run))
            self._spawn_task(run, self._await_readiness(cockpit_id, run))
            logger.info("Cockpit %s started (pid=%s, port=%s)", cockpit_id, proc.pid, port)
            return run.to_status()

    async def restart(
        self,
        *,
        cockpit_id: int,
        command: str,
        port: int,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> CockpitStatus:
        await self.stop(cockpit_id)
        return await self.start(cockpit_id=cockpit_id, command=command, port=port, cwd=cwd, env=env)

    async def stop(self, cockpit_id: int) -> CockpitStatus:
        async with self._lock(cockpit_id):
            run = self._running.get(cockpit_id)
            if run is None:
                return CockpitStatus(state="stopped")
            run.stopping = True
            if run.proc.returncode is None:
                await self._terminate(run.proc)
            for task in list(run.tasks):
                task.cancel()
            run.state = "stopped"
            self._running.pop(cockpit_id, None)
            logger.info("Cockpit %s stopped", cockpit_id)
            return CockpitStatus(state="stopped")

    async def stop_all(self) -> None:
        for cockpit_id in list(self._running.keys()):
            with contextlib.suppress(Exception):
                await self.stop(cockpit_id)

    # ---- internals -----------------------------------------------------

    async def _spawn(
        self, command: str, workdir: str | None, env: dict[str, str]
    ) -> asyncio.subprocess.Process:
        """Start ``command`` through the platform shell in its own group."""
        if sys.platform == "win32":  # pragma: no cover - platform-specific
            creationflags = getattr(__import__("subprocess"), "CREATE_NEW_PROCESS_GROUP", 0)
            return await asyncio.create_subprocess_shell(
                command,
                cwd=workdir,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=creationflags,
            )
        return await asyncio.create_subprocess_shell(
            command,
            cwd=workdir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            # New session → child is a process-group leader (pgid == pid), so a
            # single killpg reaps the whole tree (dev servers fork workers).
            start_new_session=True,
        )

    async def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        grace = get_settings().cockpits_stop_grace_seconds
        if sys.platform == "win32":  # pragma: no cover - platform-specific
            await self._terminate_windows(proc)
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=grace)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError, OSError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=grace)

    async def _terminate_windows(  # pragma: no cover - platform-specific
        self, proc: asyncio.subprocess.Process
    ) -> None:
        grace = get_settings().cockpits_stop_grace_seconds
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/F",
                "/T",
                "/PID",
                str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=grace)
        except Exception:
            with contextlib.suppress(Exception):
                proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=grace)

    def _spawn_task(self, run: _Running, coro: Coroutine[object, object, None]) -> None:
        task = asyncio.create_task(coro)
        run.tasks.add(task)
        task.add_done_callback(run.tasks.discard)

    async def _pump_output(self, cockpit_id: int, run: _Running) -> None:
        """Drain merged stdout/stderr into the ring, then detect exit."""
        limit = get_settings().cockpits_max_log_bytes
        stream = run.proc.stdout
        if stream is not None:
            try:
                while True:
                    chunk = await stream.read(4096)
                    if not chunk:
                        break
                    run.append_log(chunk, limit)
            except Exception:  # pragma: no cover - defensive
                logger.debug("Cockpit %s output pump errored", cockpit_id, exc_info=True)
        exit_code = await run.proc.wait()
        if run.stopping:
            return
        run.exit_code = exit_code
        run.state = "crashed"
        tail = run.read_log().strip().splitlines()[-3:]
        run.detail = f"process exited with code {exit_code}" + (
            f": {' / '.join(tail)}" if tail else ""
        )
        logger.info("Cockpit %s exited with code %s", cockpit_id, exit_code)

    async def _await_readiness(self, cockpit_id: int, run: _Running) -> None:
        """Flip starting → running once the port accepts a TCP connection."""
        timeout = get_settings().cockpits_readiness_timeout_seconds
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if run.proc.returncode is not None:
                return  # the output pump owns the crashed transition
            if await self._port_open(run.port):
                if run.state == "starting":
                    run.state = "running"
                    run.detail = None
                    logger.info("Cockpit %s is ready on port %s", cockpit_id, run.port)
                return
            await asyncio.sleep(_READY_PROBE_INTERVAL)
        if run.state == "starting":
            run.state = "unreachable"
            run.detail = (
                f"port {run.port} did not accept connections within {timeout}s "
                "(check the command and the declared port)"
            )
            logger.warning("Cockpit %s unreachable on port %s", cockpit_id, run.port)

    async def _port_open(self, port: int) -> bool:
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(_LOOPBACK, port),
                timeout=_READY_PROBE_CONNECT_TIMEOUT,
            )
        except (OSError, TimeoutError):
            return False
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return True


_manager: CockpitManager | None = None


def get_cockpit_manager() -> CockpitManager:
    global _manager
    if _manager is None:
        _manager = CockpitManager()
    return _manager
