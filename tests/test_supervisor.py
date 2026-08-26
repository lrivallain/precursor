"""Supervisor state handling — the contract the tray and CLI both read.

The runtime state file is the single source of truth for "is Precursor running,
and where". These tests cover the parts that must not need a real server: state
round-tripping, healing a stale file left by a crash or reboot, and refusing to
adopt a port something else already owns.
"""

from __future__ import annotations

import json
import socket
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
