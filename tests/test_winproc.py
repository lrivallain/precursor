"""Windows process plumbing.

The POSIX halves are trivial and pinned here so the platform switch can't drift;
the Windows halves run for real on the Windows CI job, which is the only place
their semantics — console control events, named mutexes, windowless spawns —
can actually be observed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from precursor.backend import winproc

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows process semantics")


def test_posix_kwargs_are_the_usual_ones() -> None:
    if os.name == "nt":  # pragma: no cover - Windows-only path
        assert winproc.no_window() == {"creationflags": winproc.CREATE_NO_WINDOW}
        flags = winproc.detached()["creationflags"]
        assert flags & winproc.CREATE_NO_WINDOW
        assert flags & winproc.CREATE_NEW_PROCESS_GROUP
    else:
        assert winproc.no_window() == {}
        assert winproc.detached() == {"start_new_session": True}


def test_spawn_detached_runs_the_command(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    proc = winproc.spawn_detached(
        [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('ok')"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert proc.wait(timeout=30) == 0
    assert marker.read_text() == "ok"


@windows_only
def test_pid_alive_tracks_a_real_process() -> None:  # pragma: no cover - Windows-only path
    assert winproc.pid_alive(os.getpid()) is True
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=30)
    assert winproc.pid_alive(child.pid) is False
    assert winproc.pid_alive(0) is False


# Waits for Ctrl+Break and records that it got to shut down on its own terms —
# the whole point of preferring it to `taskkill /F`.
_GRACEFUL_CHILD = """
import signal, sys, time
from pathlib import Path
marker = Path(sys.argv[1])
def stop(*_):
    marker.write_text("graceful")
    sys.exit(0)
signal.signal(signal.SIGBREAK, stop)
marker.write_text("ready")
time.sleep(60)
"""


@windows_only
def test_interrupt_lets_a_detached_process_shut_down_gracefully(
    tmp_path: Path,
) -> None:  # pragma: no cover - Windows-only path
    marker = tmp_path / "state"
    child = winproc.spawn_detached(
        [sys.executable, "-c", _GRACEFUL_CHILD, str(marker)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert marker.read_text() == "ready"

        assert winproc.interrupt(child.pid) is True
        child.wait(timeout=30)
        assert marker.read_text() == "graceful"
    finally:
        if child.poll() is None:
            winproc.kill_tree(child.pid)


@windows_only
def test_kill_tree_stops_a_process_that_ignores_everything() -> None:  # pragma: no cover
    child = winproc.spawn_detached(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    winproc.kill_tree(child.pid)
    child.wait(timeout=30)
    assert winproc.pid_alive(child.pid) is False


@windows_only
def test_a_named_claim_is_exclusive_until_released() -> None:  # pragma: no cover
    name = f"Local\\Precursor.test.{os.getpid()}.{time.monotonic_ns()}"
    first = winproc.acquire_single_instance(name)
    assert first is not None
    assert winproc.acquire_single_instance(name) is None
    winproc.release(first)
    again = winproc.acquire_single_instance(name)
    assert again is not None
    winproc.release(again)
