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
import logging
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

from precursor.backend.config import Settings, get_settings

logger = logging.getLogger(__name__)

# How long to wait for a freshly spawned instance to accept connections before
# giving up and reporting the log file. Generous: the first start of a new
# version applies Alembic migrations.
_START_TIMEOUT_SECONDS = 90.0
# How long to wait for a graceful SIGTERM before escalating to SIGKILL.
_STOP_TIMEOUT_SECONDS = 20.0
# The setting that pins the port, in both the environment and the instance `.env`.
_PORT_ENV_VAR = "PRECURSOR_PORT"


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


def instance_settings() -> Settings:
    """Settings as the *supervised child* will resolve them.

    ``pydantic-settings`` reads ``.env`` relative to the process working
    directory, but the CLI and the tray can be invoked from anywhere while the
    instance always runs in :func:`working_dir`. Resolving from the caller's
    directory would make the port depend on where you happened to type the
    command — and then hand the child an explicit ``--port`` that overrides the
    ``.env`` it would have read itself.
    """
    env_file = _env_file()
    if env_file.is_file():
        return Settings(_env_file=env_file)
    return get_settings()


def _env_file() -> Path:
    """The ``.env`` the supervised instance reads (it runs in :func:`working_dir`)."""
    return working_dir() / ".env"


def _env_file_port() -> int | None:
    """The port assigned by an uncommented ``PRECURSOR_PORT`` line, if any."""
    try:
        lines = _env_file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if sep and key.strip().upper() == _PORT_ENV_VAR:
            try:
                return int(value.strip().strip("\"'"))
            except ValueError:
                return None
    return None


def port_is_pinned() -> bool:
    """Whether somebody chose the port deliberately, rather than defaulting to it.

    A deliberate choice is one to honour or fail loudly about — moving it would
    break whatever depends on that number. The default is only a starting guess,
    so it may be bumped when the port turns out to be taken.
    """
    if os.environ.get(_PORT_ENV_VAR, "").strip():
        return True
    return _env_file_port() is not None


def _free_port(host: str, preferred: int) -> int:
    """The first bindable port at or above ``preferred`` on ``host``."""
    from precursor.backend.__main__ import _resolve_port

    try:
        return _resolve_port(host, preferred, strict=False)
    except SystemExit as exc:  # exhausted the scan window
        raise SupervisorError(str(exc)) from exc


def _persist_port(port: int) -> Path:
    """Write ``PRECURSOR_PORT`` into the instance's ``.env`` and return its path.

    The login item is started by launchd/systemd with no arguments, so the port
    has to live somewhere the child re-reads on its own — and ``.env`` in
    :func:`working_dir` is exactly the file it already loads. Recording it also
    makes the choice sticky: the URL stays the same across restarts and reboots
    instead of drifting with whatever else happens to hold a port that day.
    """
    path = _env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []

    assignment = f"{_PORT_ENV_VAR}={port}"
    for index, line in enumerate(lines):
        key, sep, _ = line.strip().partition("=")
        if sep and key.strip().upper() == _PORT_ENV_VAR:
            lines[index] = assignment
            break
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [
            "# Picked by `precursor service install` — the default port was busy.",
            assignment,
        ]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    get_settings.cache_clear()
    return path


@dataclass(frozen=True)
class PortReservation:
    """The port a fresh install settled on, and where it was recorded."""

    port: int
    moved_from: int | None = None
    env_file: str | None = None


def reserve_port(*, host: str | None = None, port: int | None = None) -> PortReservation:
    """Settle on a port the instance can actually bind, before registering it.

    Installing is most people's *first* run, and 8000 is a popular port: some
    other dev server holding it used to leave the login item crash-looping
    against ``--strict-port`` and the installer reporting nothing but "did not
    come up". So an unpinned default that is already taken is moved to the next
    free port and written to ``.env``, while a port somebody actually chose is
    left alone — a deliberate choice deserves an error, not a silent move.
    """
    cfg = instance_settings()
    host = host or cfg.host
    current = status()
    if port is None and current.running and current.state is not None:
        # Already serving: adopt its port rather than reserving a second one.
        return PortReservation(port=current.state.port)

    desired = port if port is not None else cfg.port
    if _port_free_for(host, desired):
        if port is not None and port != cfg.port:
            return PortReservation(port=port, env_file=str(_persist_port(port)))
        return PortReservation(port=desired)

    if port is not None or port_is_pinned():
        raise SupervisorError(
            f"Port {desired} is already in use by something Precursor didn't start. "
            "Free it, or install on another port with "
            "`precursor service install --port <port>`."
        )

    chosen = _free_port(host, desired + 1)
    return PortReservation(port=chosen, moved_from=desired, env_file=str(_persist_port(chosen)))


def _port_free_for(host: str, port: int) -> bool:
    from precursor.backend.__main__ import _port_free

    return _port_free(_loopback_host(host), port)


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


def _clear_state(*, only_if_pid: int | None = None) -> None:
    """Remove the runtime state file.

    ``only_if_pid`` guards against a losing process deleting the record of a
    *different*, healthy instance — which is how ``service status`` ends up
    reporting "not running" while something is plainly serving.
    """
    if only_if_pid is not None:
        state = _read_state()
        if state is not None and state.pid != only_if_pid:
            return
    with contextlib.suppress(OSError):
        _state_path().unlink()


def managed_unit() -> Any | None:
    """The login item that owns this instance's lifecycle, if there is one.

    When a launchd agent or systemd unit is installed, *it* is the supervisor:
    killing the process is precisely what its KeepAlive/Restart directive exists
    to undo. So stop and restart have to be asked of the service manager. Doing
    it directly means the manager immediately starts a replacement while we
    start our own, and the two race for the port — one wins, the loser retries
    on a throttle forever.
    """
    try:
        from precursor.backend import autostart

        info = autostart.info(autostart.APP)
        return autostart.APP if info.controllable else None
    except Exception:  # pragma: no cover - never let this break start/stop
        logger.debug("Could not determine the autostart unit", exc_info=True)
        return None


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

    cfg = instance_settings()
    explicit_port = port is not None
    host = host or cfg.host
    port = port if port is not None else cfg.port
    log_level = log_level or cfg.log_level

    # A registered login item owns the process. Ask it to start rather than
    # spawning a second one it would then fight for the port. An explicit
    # host/port override is the exception: the unit has its own configuration,
    # so honouring the override means running it ourselves.
    unit = (
        managed_unit() if (host, port, log_level) == (cfg.host, cfg.port, cfg.log_level) else None
    )
    if unit is not None:
        from precursor.backend import autostart

        autostart.start_unit(unit)
        settled = _await_state(port=port, host=host)
        if settled.running:
            return settled
        detail = (
            f" Port {port} is held by something Precursor didn't start — free it, or move "
            "Precursor with `precursor service install --port <port>`."
            if _port_responds(_loopback_host(host), port)
            else ""
        )
        raise SupervisorError(
            f"The {unit.title} login item did not come up on port {port}. "
            f"See {_log_path()}.{detail}"
        )

    if _port_responds(_loopback_host(host), port):
        if explicit_port or port_is_pinned():
            raise SupervisorError(
                f"Port {port} is already in use by something Precursor didn't start. "
                "Free it, or pick another with --port."
            )
        # Nobody chose this port — it is just the default, and a first run
        # shouldn't fail because some other dev server got to 8000 first.
        bumped = _free_port(host, port + 1)
        logger.warning("Port %s is in use — starting Precursor on %s instead.", port, bumped)
        port = bumped

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


def _await_state(*, port: int, host: str, timeout: float = _START_TIMEOUT_SECONDS) -> Status:
    """Wait for a service-manager-started instance to publish its state.

    The unit's own process writes ``runtime.json`` once it is listening (see
    :func:`run_foreground`), so the supervisor watches for that rather than
    guessing a pid it never spawned.
    """
    connect_host = _loopback_host(host)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = status()
        if current.running and _port_responds(connect_host, current.state.port, timeout=0.3):  # type: ignore[union-attr]
            return current
        time.sleep(0.25)
    return status()


def _loopback_host(host: str) -> str:
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


def stop(*, timeout: float = _STOP_TIMEOUT_SECONDS) -> bool:
    """Stop the supervised instance. Returns False if it wasn't running."""
    unit = managed_unit()
    if unit is not None:
        from precursor.backend import autostart

        was_running = status().running
        # Boot the job out rather than signalling it: a plain kill is exactly
        # what KeepAlive undoes, and the replacement would then collide with
        # whatever starts next.
        autostart.stop_unit(unit)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not status().running:
                break
            time.sleep(0.2)
        _clear_state()
        return was_running

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
    cfg = instance_settings()
    overridden = any(
        value is not None and value != default
        for value, default in ((host, cfg.host), (port, cfg.port), (log_level, cfg.log_level))
    )
    unit = managed_unit() if not overridden else None
    if unit is not None:
        from precursor.backend import autostart

        # One atomic operation, so nothing can claim the port in the gap
        # between the old process dying and the new one binding — which is
        # exactly the race a stop-then-start loses.
        autostart.restart_unit(unit)
        settled = _await_state(port=port if port is not None else cfg.port, host=host or cfg.host)
        if settled.running:
            return settled
        raise SupervisorError(
            f"The {unit.title} login item did not come back up. See {_log_path()}."
        )

    previous = _read_state()
    stop()
    return start(
        host=host or (previous.host if previous else None),
        port=port if port is not None else (previous.port if previous else None),
        log_level=log_level,
    )


def restartable() -> tuple[bool, str]:
    """Whether *this* instance can restart itself, and why not when it can't.

    Only a supervised instance can: :func:`restart` works by reading
    ``runtime.json`` to find and stop the running process, and a stack started
    with ``uv run precursor --dev`` publishes no such state (it is owned by the
    terminal it runs in, and Vite alongside it). Saying so is more useful than a
    button that silently does nothing.

    Deliberately **read-only**, unlike :func:`status`, which deletes the state
    file when it judges the recorded pid dead. This is a capability probe served
    from a polled HTTP endpoint, and a probe must not evict a running instance's
    own bookkeeping — a single misread would leave a live server reporting "not
    running" to `service status` and the tray.
    """
    state = _read_state()
    if state is None or not _pid_alive(state.pid):
        return False, (
            "This instance isn't supervised, so it can't restart itself — "
            "restart it the way you started it."
        )
    return True, "ready"


def request_detached_restart() -> None:
    """Restart this instance from a **detached** child process.

    The child is about to stop the very process spawning it, so it must not be
    in our process group or die with us — hence :func:`_spawn_kwargs` and fully
    redirected stdio. ``-m precursor.backend`` rather than the console script,
    for the same reason :func:`_child_command` uses it: the running interpreter
    is guaranteed to have the package importable.
    """
    ok, detail = restartable()
    if not ok:
        raise SupervisorError(detail)
    subprocess.Popen(
        [sys.executable, "-m", "precursor.backend", "service", "restart"],
        cwd=str(working_dir()),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **_spawn_kwargs(),
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

    # A login item and a manual `service start` can both fire — on install, the
    # launchd/systemd unit starts at load while the user may already have one
    # running. Binding regardless would hit --strict-port and exit non-zero,
    # which KeepAlive reads as a crash and retries forever. Exiting *successfully*
    # says "already served, nothing to do" and ends the loop.
    current = status()
    if current.running and current.state is not None:
        logger.info(
            "Precursor is already running on %s (pid %s) — nothing to do.",
            current.state.url,
            current.state.pid,
        )
        return

    # Become the process the detached path would have spawned: Popen gives that
    # child ``cwd=working_dir()``, and settings (``.env``, and a checkout's
    # relative database URL) resolve against the working directory — so adopt it
    # here too, then re-read settings so both paths land on identical values.
    os.chdir(working_dir())
    get_settings.cache_clear()
    cfg = get_settings()
    host = host or cfg.host
    log_level = log_level or cfg.log_level
    # Strict only for an explicit ``--port``: that is somebody watching a
    # terminal, and exiting non-zero is how they learn the port was wrong. The
    # login item passes no port, and for *it* a refusal is a crash loop —
    # KeepAlive/Restart retrying forever against a port someone else owns. It
    # would rather run somewhere and say so: `runtime.json` publishes the real
    # URL, which is what `service status` and the tray read anyway.
    preferred = port if port is not None else cfg.port
    resolved = _resolve_port(host, preferred, strict=port is not None)
    if resolved != preferred:
        logger.warning(
            "Port %s is in use — Precursor is starting on %s instead.", preferred, resolved
        )
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
        # Only if the record is still ours. A losing process must not delete
        # the state of a different, healthy instance.
        _clear_state(only_if_pid=os.getpid())
