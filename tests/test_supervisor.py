"""Supervisor state handling — the contract the tray and CLI both read.

The runtime state file is the single source of truth for "is Precursor running,
and where". These tests cover the parts that must not need a real server: state
round-tripping, healing a stale file left by a crash or reboot, and refusing to
adopt a port something else already owns.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from precursor.backend import config, supervisor


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("PRECURSOR_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def _state(pid: int, port: int = 8123) -> supervisor.RuntimeState:
    return supervisor.RuntimeState(
        pid=pid,
        host="127.0.0.1",
        port=port,
        url=f"http://127.0.0.1:{port}/",
        version="2026.1.0",
        started_at="2026-01-01T00:00:00+00:00",
        log_file="/tmp/precursor.log",
    )


def test_status_is_not_running_without_a_state_file() -> None:
    status = supervisor.status()
    assert status.running is False
    assert status.state is None
    assert status.stale is False


def test_state_round_trips(tmp_path: Path) -> None:
    import os

    supervisor._write_state(_state(os.getpid()))
    read = supervisor._read_state()
    assert read is not None
    assert read.port == 8123
    assert read.pid == os.getpid()


def test_running_process_reports_running() -> None:
    import os

    supervisor._write_state(_state(os.getpid()))
    status = supervisor.status()
    assert status.running is True
    assert status.state is not None
    assert status.state.url == "http://127.0.0.1:8123/"


def test_stale_state_is_healed() -> None:
    # PID 1 exists everywhere, so use an implausible one that cannot be alive.
    supervisor._write_state(_state(2**22))
    status = supervisor.status()
    assert status.running is False
    assert status.stale is True
    # A crashed instance must not leave a file claiming a port it no longer owns.
    assert not Path(config.get_settings().runtime_state_file).exists()


def test_corrupt_state_is_ignored() -> None:
    path = Path(config.get_settings().runtime_state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")
    assert supervisor._read_state() is None
    assert supervisor.status().running is False


def test_partial_state_is_ignored() -> None:
    path = Path(config.get_settings().runtime_state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": 1}), encoding="utf-8")
    assert supervisor._read_state() is None


def test_start_refuses_a_port_owned_by_something_else() -> None:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        with pytest.raises(supervisor.SupervisorError, match="already in use"):
            supervisor.start(host="127.0.0.1", port=port)
    finally:
        listener.close()


def test_stop_is_a_no_op_when_not_running() -> None:
    assert supervisor.stop() is False


def test_state_is_not_cleared_by_a_process_that_does_not_own_it() -> None:
    """A losing process must not delete a healthy instance's record.

    Otherwise `service status` reports "not running" while something is plainly
    serving, and the next start races whatever already holds the port.
    """
    import os

    supervisor._write_state(_state(os.getpid()))
    supervisor._clear_state(only_if_pid=os.getpid() + 12345)
    assert supervisor._read_state() is not None

    supervisor._clear_state(only_if_pid=os.getpid())
    assert supervisor._read_state() is None


def test_stop_asks_the_service_manager_when_one_owns_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """launchd's KeepAlive (and systemd's Restart=) undo a plain kill.

    Signalling the process makes the manager start a replacement, which then
    collides with whatever the supervisor starts next: one wins the port and the
    loser retries on a throttle forever. So stop has to go through the manager.
    """
    import os

    from precursor.backend import autostart

    supervisor._write_state(_state(os.getpid()))
    calls: list[str] = []
    monkeypatch.setattr(supervisor, "managed_unit", lambda: autostart.APP)
    monkeypatch.setattr(autostart, "stop_unit", lambda _unit=autostart.APP: calls.append("stop"))

    def _must_not_signal(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("stop() signalled a service-manager-owned process")

    monkeypatch.setattr(supervisor.os, "kill", _must_not_signal)
    # Alive before the manager stops it, gone afterwards.
    alive = iter([True])
    monkeypatch.setattr(supervisor, "_pid_alive", lambda _pid: next(alive, False))

    assert supervisor.stop(timeout=2) is True
    assert calls == ["stop"]


def test_restart_delegates_atomically_when_a_unit_owns_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stop-then-start leaves a window for the manager's own replacement to
    take the port; `launchctl kickstart -k` closes it."""
    from precursor.backend import autostart

    calls: list[str] = []
    monkeypatch.setattr(supervisor, "managed_unit", lambda: autostart.APP)
    monkeypatch.setattr(
        autostart, "restart_unit", lambda _unit=autostart.APP: calls.append("restart")
    )
    monkeypatch.setattr(
        supervisor,
        "_await_state",
        lambda **_kw: supervisor.Status(running=True, state=_state(1234)),
    )

    def _must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("restart() stopped and started around the manager's back")

    monkeypatch.setattr(supervisor, "stop", _must_not_run)
    monkeypatch.setattr(supervisor, "start", _must_not_run)

    assert supervisor.restart().running is True
    assert calls == ["restart"]


def test_an_explicit_port_override_bypasses_the_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The unit carries its own configuration, so honouring `--port` means
    running the instance ourselves rather than asking the manager."""
    from precursor.backend import autostart

    monkeypatch.setattr(supervisor, "managed_unit", lambda: autostart.APP)
    monkeypatch.setattr(
        autostart,
        "restart_unit",
        lambda _unit=autostart.APP: (_ for _ in ()).throw(
            AssertionError("delegated despite an explicit override")
        ),
    )
    seen: dict[str, object] = {}
    monkeypatch.setattr(supervisor, "stop", lambda: True)
    monkeypatch.setattr(
        supervisor,
        "start",
        lambda **kw: seen.update(kw) or supervisor.Status(running=True, state=_state(1)),
    )

    supervisor.restart(port=9999)
    assert seen["port"] == 9999


def test_a_windows_startup_entry_is_not_treated_as_a_service_manager() -> None:
    """It is a shortcut run at login, not something that can be asked to stop."""
    from precursor.backend import autostart

    entry = autostart.AutostartInfo(
        unit="app", supported=True, installed=True, kind="startup-folder"
    )
    assert entry.controllable is False
    for kind in ("launchd", "systemd"):
        managed = autostart.AutostartInfo(unit="app", supported=True, installed=True, kind=kind)
        assert managed.controllable is True
    absent = autostart.AutostartInfo(unit="app", supported=True, installed=False, kind="launchd")
    assert absent.controllable is False


@pytest.mark.skipif(sys.platform != "darwin", reason="launchctl semantics")
def test_unit_control_bootstraps_a_job_launchd_does_not_know_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plist on disk is not the same as a loaded job.

    `kickstart` only works on a job launchd already knows about, and the two
    diverge whenever the instance was stopped or its executable was replaced
    underneath it. Both start and restart must fall back to bootstrapping
    rather than reporting "Could not find service".
    """
    from precursor.backend import autostart

    for control in (autostart.start_unit, autostart.restart_unit):
        commands: list[list[str]] = []

        def fake_run(
            cmd: list[str], _sink: list[list[str]] = commands, **_kw: object
        ) -> subprocess.CompletedProcess[str]:
            _sink.append(cmd)
            # Mimic launchctl refusing a job it has never loaded.
            failed = "kickstart" in cmd
            return subprocess.CompletedProcess(
                cmd, 1 if failed else 0, stdout="", stderr="Could not find service"
            )

        monkeypatch.setattr(autostart.subprocess, "run", fake_run)
        monkeypatch.setattr(autostart, "_target_path", lambda _u: pathlib.Path("/tmp/x.plist"))
        control(autostart.APP)

        assert any("kickstart" in c for c in commands), control.__name__
        assert any("bootstrap" in c for c in commands), (
            f"{control.__name__} gave up instead of loading the job"
        )


def test_run_foreground_yields_to_an_existing_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """A login item starting while an instance already runs must exit *0*.

    Binding regardless would hit --strict-port and exit non-zero, which
    launchd's KeepAlive reads as a crash — producing a retry loop every 30s
    forever rather than a no-op.
    """
    import os

    supervisor._write_state(_state(os.getpid()))

    def _must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("run_foreground tried to bind over a live instance")

    monkeypatch.setattr(supervisor, "_resolve_port", _must_not_run, raising=False)

    supervisor.run_foreground()  # returns cleanly == exit 0


def test_run_foreground_bumps_rather_than_crash_looping(
    monkeypatch: pytest.MonkeyPatch, _instance_dir: Path
) -> None:
    """The login item must never refuse to start over a busy port.

    launchd's KeepAlive and systemd's Restart read a non-zero exit as a crash
    and retry forever, so a port someone else owns would leave Precursor down
    and looping. It runs on the next free port instead and publishes it.
    """
    listener, port = _busy_port()
    (_instance_dir / ".env").write_text(f"PRECURSOR_PORT={port}\n", encoding="utf-8")
    config.get_settings.cache_clear()
    served: dict[str, int] = {}
    monkeypatch.setattr(
        "precursor.backend.__main__._run_prod",
        lambda _host, p, *_a, **_k: served.setdefault("port", p),
    )
    cwd = os.getcwd()
    try:
        supervisor.run_foreground()
    finally:
        os.chdir(cwd)
        listener.close()
        config.get_settings.cache_clear()

    assert served["port"] > port


def test_settings_come_from_the_instance_dir_not_the_callers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The CLI and the tray can be invoked from anywhere, but the instance
    always runs in ``working_dir()``.

    Resolving ``.env`` from the caller's directory instead would make the port
    depend on where you happened to type the command — and then hand the child
    an explicit ``--port`` overriding the ``.env`` it would have read itself.
    """
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    (instance_dir / ".env").write_text("PRECURSOR_PORT=9000\n", encoding="utf-8")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(supervisor, "working_dir", lambda: instance_dir)

    assert supervisor.instance_settings().port == 9000


def test_instance_settings_fall_back_when_there_is_no_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(supervisor, "working_dir", lambda: tmp_path / "empty")
    assert supervisor.instance_settings().port == config.get_settings().port


@pytest.fixture
def _instance_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An empty, throwaway ``working_dir()`` — the `.env` reserve_port writes to."""
    instance = tmp_path / "instance"
    instance.mkdir()
    monkeypatch.delenv("PRECURSOR_PORT", raising=False)
    monkeypatch.setattr(supervisor, "working_dir", lambda: instance)
    return instance


def _busy_port() -> tuple[socket.socket, int]:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    return listener, listener.getsockname()[1]


def test_reserve_port_keeps_a_free_default_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, _instance_dir: Path
) -> None:
    listener, port = _busy_port()
    listener.close()  # ...so the "configured" port is known-free
    monkeypatch.setattr(supervisor, "instance_settings", lambda: config.Settings(port=port))

    reservation = supervisor.reserve_port()
    assert reservation.port == port
    assert reservation.moved_from is None
    assert reservation.env_file is None
    assert not (_instance_dir / ".env").exists()


def test_reserve_port_moves_a_busy_default_and_records_it(
    monkeypatch: pytest.MonkeyPatch, _instance_dir: Path
) -> None:
    """The bug a naive first install hits: something else already owns 8000.

    Registering the login item against a port it can never bind leaves
    launchd/systemd retrying a doomed start forever, so the installer must
    settle on a free port *before* the unit exists — and persist it, or the
    unit (started with no arguments) would read the busy default right back.
    """
    listener, port = _busy_port()
    monkeypatch.setattr(supervisor, "instance_settings", lambda: config.Settings(port=port))
    try:
        reservation = supervisor.reserve_port()
    finally:
        listener.close()

    assert reservation.moved_from == port
    assert reservation.port > port
    assert reservation.env_file == str(_instance_dir / ".env")
    assert f"PRECURSOR_PORT={reservation.port}" in (_instance_dir / ".env").read_text()


def test_reserve_port_replaces_a_previously_reserved_line(
    monkeypatch: pytest.MonkeyPatch, _instance_dir: Path
) -> None:
    """Re-installing must not stack a second assignment the last one shadows."""
    env = _instance_dir / ".env"
    env.write_text("PRECURSOR_LOG_LEVEL=debug\nPRECURSOR_PORT=8000\n", encoding="utf-8")
    supervisor._persist_port(8123)
    body = env.read_text(encoding="utf-8")
    assert body.count("PRECURSOR_PORT=") == 1
    assert "PRECURSOR_PORT=8123" in body
    assert "PRECURSOR_LOG_LEVEL=debug" in body


def test_reserve_port_refuses_to_move_a_port_somebody_chose(
    monkeypatch: pytest.MonkeyPatch, _instance_dir: Path
) -> None:
    """A pinned port is a decision: report it, don't silently serve elsewhere."""
    listener, port = _busy_port()
    (_instance_dir / ".env").write_text(f"PRECURSOR_PORT={port}\n", encoding="utf-8")
    try:
        with pytest.raises(supervisor.SupervisorError, match="already in use"):
            supervisor.reserve_port()
    finally:
        listener.close()


def test_start_bumps_a_busy_default_port(monkeypatch: pytest.MonkeyPatch, _instance_dir: Path):
    """Nobody chose the default, so a first run shouldn't die on someone else's server."""
    listener, port = _busy_port()
    monkeypatch.setattr(supervisor, "instance_settings", lambda: config.Settings(port=port))
    spawned: dict[str, int] = {}

    class _Proc:
        pid = 4242

        def poll(self) -> None:
            return None

    def _fake_popen(cmd: list[str], **_kwargs: object) -> _Proc:
        spawned["port"] = int(cmd[cmd.index("--port") + 1])
        return _Proc()

    monkeypatch.setattr(supervisor.subprocess, "Popen", _fake_popen)
    # Always "listening": the busy default is what triggers the bump, and the
    # spawned child answering on the new port is what ends the readiness wait.
    monkeypatch.setattr(supervisor, "_port_responds", lambda *_a, **_k: True)
    monkeypatch.setattr(supervisor, "managed_unit", lambda: None)
    try:
        status = supervisor.start()
    finally:
        listener.close()

    assert spawned["port"] > port
    assert status.state is not None
    assert status.state.port == spawned["port"]
