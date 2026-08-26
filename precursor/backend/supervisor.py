"""Background-instance supervisor for the ``precursor service`` commands.

Precursor normally runs in the foreground of a terminal. That is fine for
development, but it makes "have it there when I log in" a chore: something has
to own the process, remember where it landed, and be able to stop or replace it
later. This module is that owner.

It keeps a single **runtime state file** (``<data_dir>/runtime.json``) recording
the detached child's pid, host and port. Everything else — ``status``, ``stop``,
``restart``, the tray icon — is derived from that file plus a liveness probe, so
there is exactly one source of truth and no port guessing.

Scope is deliberately one instance *per data directory*. A source checkout keeps
its data beside the code, so every worktree supervises its own instance and
dev servers on auto-bumped ports never collide with the installed one.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from precursor.backend.config import get_settings

# How long to wait for a freshly spawned instance to accept connections before
# giving up and reporting the log file. Generous: the first start of a new
# version applies Alembic migrations.
_START_TIMEOUT_SECONDS = 90.0
# How long to wait for a graceful SIGTERM before escalating to SIGKILL.
_STOP_TIMEOUT_SECONDS = 20.0


class SupervisorError(RuntimeError):
    """A start/stop operation could not be completed."""


@dataclass(frozen=True)
class RuntimeState:
    """What the supervisor recorded about the instance it started."""

    pid: int
    host: str
    port: int
    url: str
    version: str
    started_at: str
    log_file: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "host": self.host,
            "port": self.port,
            "url": self.url,
            "version": self.version,
            "started_at": self.started_at,
            "log_file": self.log_file,
        }


@dataclass(frozen=True)
class Status:
    running: bool
    state: RuntimeState | None
    # Set when a state file exists but the process behind it is gone.
    stale: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "stale": self.stale,
            **({"instance": self.state.as_dict()} if self.state else {}),
        }


def _state_path() -> Path:
    return Path(get_settings().runtime_state_file)


def _log_path() -> Path:
    return Path(get_settings().logs_dir) / "precursor.log"


def working_dir() -> Path:
    """The directory a supervised instance should run in.

    A launcher hands the process its own working directory — ``/`` for a launchd
    agent — so one has to be chosen explicitly. It matters beyond tidiness in a
    source checkout, where the default database URL is *relative*
    (``./precursor.db``): anchoring anywhere else would quietly give the
    supervised instance a different database than ``uv run precursor`` uses in
    the same clone. So a checkout anchors at the repo root, and an installed
    wheel — whose defaults are already absolute — at its data directory.
    """
    from precursor.backend.config import is_source_checkout

    if is_source_checkout():
        return Path(__file__).resolve().parents[2]
    return Path(get_settings().data_dir).resolve()


def _read_state() -> RuntimeState | None:
    path = _state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return RuntimeState(
            pid=int(raw["pid"]),
            host=str(raw["host"]),
            port=int(raw["port"]),
            url=str(raw["url"]),
            version=str(raw.get("version", "")),
            started_at=str(raw.get("started_at", "")),
            log_file=str(raw.get("log_file", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _write_state(state: RuntimeState) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-rename so a reader (the tray polls this file) never observes a
    # half-written record.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state.as_dict(), indent=2), encoding="utf-8")
    tmp.replace(path)


def _clear_state() -> None:
    with contextlib.suppress(OSError):
        _state_path().unlink()


def _pid_alive(pid: int) -> bool:
    """Whether ``pid`` still exists (not whether it is *our* process)."""
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - Windows-only path
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but belongs to someone else.
        return True
    return True


def _port_responds(host: str, port: int, *, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def status() -> Status:
    """Report whether the supervised instance is alive, healing stale state."""
    state = _read_state()
    if state is None:
        return Status(running=False, state=None)
    if not _pid_alive(state.pid):
        # The process died (crash, reboot, `kill`). Don't leave a state file
        # claiming a port that something else may now own.
        _clear_state()
        return Status(running=False, state=state, stale=True)
    return Status(running=True, state=state)


def _child_command(port: int, host: str, *, log_level: str) -> list[str]:
    # `-m precursor.backend` rather than the `precursor` console script: the
    # running interpreter is guaranteed to have the package importable, while a
    # console script may not be on a launchd/systemd PATH.
    return [
        sys.executable,
        "-m",
        "precursor.backend",
        "--host",
        host,
        "--port",
        str(port),
        "--strict-port",
        "--log-level",
        log_level,
    ]


def _spawn_kwargs() -> dict[str, Any]:
    """Detach the child so it outlives the CLI/tray process that started it."""
    if os.name == "nt":  # pragma: no cover - Windows-only path
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        return {"creationflags": 0x00000008 | 0x00000200}
    return {"start_new_session": True}


def start(
    *,
    host: str | None = None,
    port: int | None = None,
    log_level: str | None = None,
) -> Status:
    """Start a detached instance, or return the existing one untouched.

    Starting is idempotent on purpose: a login item, a tray click and a manual
    ``precursor service start`` can all race, and none of them should end up
    with two instances fighting over one database.
    """
    current = status()
    if current.running:
        return current

    cfg = get_settings()
    host = host or cfg.host
    port = port if port is not None else cfg.port
    log_level = log_level or cfg.log_level

    if _port_responds(host if host not in ("0.0.0.0", "::", "") else "127.0.0.1", port):
        raise SupervisorError(
            f"Port {port} is already in use by something Precursor didn't start. "
            "Free it, or pick another with --port."
        )

    log_file = _log_path()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handle = log_file.open("ab")
    try:
        handle.write(
            f"\n--- precursor service start {datetime.now(UTC).isoformat()} ---\n".encode()
        )
        handle.flush()
        proc = subprocess.Popen(
            _child_command(port, host, log_level=log_level),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(working_dir()),
            **_spawn_kwargs(),
        )
    finally:
        handle.close()

    connect_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    deadline = time.monotonic() + _START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SupervisorError(
                f"Precursor exited immediately (code {proc.returncode}). See {log_file}."
            )
        if _port_responds(connect_host, port):
            break
        time.sleep(0.25)
    else:
        with contextlib.suppress(Exception):
            proc.terminate()
        raise SupervisorError(
            f"Precursor did not start listening on {connect_host}:{port} within "
            f"{int(_START_TIMEOUT_SECONDS)}s. See {log_file}."
        )

    from precursor import __version__

    state = RuntimeState(
        pid=proc.pid,
        host=host,
        port=port,
        url=f"http://{connect_host}:{port}/",
        version=__version__,
        started_at=datetime.now(UTC).isoformat(),
        log_file=str(log_file),
    )
    _write_state(state)
    return Status(running=True, state=state)


def stop(*, timeout: float = _STOP_TIMEOUT_SECONDS) -> bool:
    """Stop the supervised instance. Returns False if it wasn't running."""
    current = status()
    if not current.running or current.state is None:
        _clear_state()
        return False

    pid = current.state.pid
    try:
        if os.name == "nt":  # pragma: no cover - Windows-only path
            subprocess.run(["taskkill", "/PID", str(pid), "/T"], check=False, capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _clear_state()
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            _clear_state()
            return True
        time.sleep(0.2)

    # Graceful shutdown overran (a wedged SSE stream, say) — insist.
    with contextlib.suppress(ProcessLookupError, OSError):
        if os.name == "nt":  # pragma: no cover - Windows-only path
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True
            )
        else:
            os.kill(pid, signal.SIGKILL)
    _clear_state()
    return True


def restart(
    *,
    host: str | None = None,
    port: int | None = None,
    log_level: str | None = None,
) -> Status:
    previous = _read_state()
    stop()
    return start(
        host=host or (previous.host if previous else None),
        port=port if port is not None else (previous.port if previous else None),
        log_level=log_level,
    )


def run_foreground(
    *,
    host: str | None = None,
    port: int | None = None,
    log_level: str | None = None,
) -> None:
    """Run the app in *this* process while still publishing runtime state.

    This is what a launchd agent or systemd unit executes: those supervisors
    want to own the process themselves, but the tray and ``service status``
    still need to know where the instance landed — so we record the same state
    file a detached start would, keyed to our own pid.
    """
    # Imported lazily: __main__ imports uvicorn and owns the banner, and it
    # imports nothing from here, so this keeps the dependency one-directional.
    from precursor import __version__
    from precursor.backend.__main__ import _loopback, _resolve_port, _run_prod

    cfg = get_settings()
    host = host or cfg.host
    log_level = log_level or cfg.log_level
    resolved = _resolve_port(host, port if port is not None else cfg.port, strict=True)
    connect_host = _loopback(host)

    _write_state(
        RuntimeState(
            pid=os.getpid(),
            host=host,
            port=resolved,
            url=f"http://{connect_host}:{resolved}/",
            version=__version__,
            started_at=datetime.now(UTC).isoformat(),
            log_file=str(_log_path()),
        )
    )
    try:
        _run_prod(host, resolved, log_level, strict_port=True, open_browser=False)
    finally:
        _clear_state()
