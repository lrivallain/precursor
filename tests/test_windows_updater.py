"""The helper that finishes a Windows update from outside the tool environment.

It is plain stdlib Python so it can run while the package is being replaced, and
nothing about its sequencing is Windows-specific — so it is exercised here on
every platform with stand-in commands.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from precursor.backend import windows_updater


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def _write(path: Path, text: str = "ran") -> list[str]:
    return _py(f"open({str(path)!r}, 'w').write({text!r})")


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(windows_updater, "_RETRY_DELAY_SECONDS", 0.0)


def _job(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "wait_pids": [],
        "commands": [_py("print('installed')")],
        "summaries": ["Installed 2026.9.2."],
        "app_command": None,
        "tray_command": None,
        "cwd": str(tmp_path),
        "log": str(tmp_path / "update.log"),
        "result": str(tmp_path / "result.json"),
        "from_version": "2026.9.1",
        "to_version": "2026.9.2",
    }
    job.update(overrides)
    return job


def test_a_successful_install_restarts_the_app_and_the_tray(tmp_path: Path) -> None:
    app, icon = tmp_path / "app", tmp_path / "tray"
    job = _job(tmp_path, app_command=_write(app), tray_command=_write(icon))

    result = windows_updater.run(job, io.StringIO())

    assert result["ok"] is True
    assert result["message"] == "Installed 2026.9.2."
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))["ok"] is True
    assert app.read_text() == "ran"  # waited on
    deadline = time.monotonic() + 30  # spawned, not waited on
    while not icon.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert icon.read_text() == "ran"


def test_the_fallback_install_is_reported_as_such(tmp_path: Path) -> None:
    job = _job(
        tmp_path,
        commands=[_py("import sys; sys.exit(3)"), _py("pass")],
        summaries=["Installed.", "Installed. Skipped kanban."],
    )
    result = windows_updater.run(job, io.StringIO())
    assert result["ok"] is True
    assert result["message"] == "Installed. Skipped kanban."


def test_a_failed_install_still_brings_the_app_back(tmp_path: Path) -> None:
    """A stopped app nobody restarts is worse than the previous build."""
    app = tmp_path / "app"
    failing = _py("import sys; sys.stderr.write('No solution found\\n'); sys.exit(1)")
    job = _job(tmp_path, commands=[failing], app_command=_write(app))

    log = io.StringIO()
    result = windows_updater.run(job, log)

    assert result["ok"] is False
    assert "No solution found" in result["message"]
    assert app.read_text() == "ran"
    assert "Update failed" in log.getvalue()


def test_files_still_in_use_are_retried(tmp_path: Path) -> None:
    """The launchers around the caller exit a beat after it does."""
    counter = tmp_path / "attempts"
    flaky = _py(
        "import sys; from pathlib import Path\n"
        f"p = Path({str(counter)!r}); n = int(p.read_text()) if p.exists() else 0\n"
        "p.write_text(str(n + 1))\n"
        "if n < 2:\n"
        "    sys.stderr.write('failed to remove file: Access is denied. (os error 5)\\n')\n"
        "    sys.exit(2)\n"
    )
    result = windows_updater.run(_job(tmp_path, commands=[flaky]), io.StringIO())
    assert result["ok"] is True
    assert counter.read_text() == "3"


def test_an_ordinary_failure_is_not_retried(tmp_path: Path) -> None:
    counter = tmp_path / "attempts"
    failing = _py(
        "import sys; from pathlib import Path\n"
        f"p = Path({str(counter)!r}); p.write_text(p.read_text() + 'x' if p.exists() else 'x')\n"
        "sys.exit(1)\n"
    )
    windows_updater.run(_job(tmp_path, commands=[failing]), io.StringIO())
    assert counter.read_text() == "x"


def test_it_waits_for_the_caller_to_exit(tmp_path: Path) -> None:
    caller = subprocess.Popen(_py("import time; time.sleep(1.5)"))
    # Reaped as it exits: an unreaped child lingers as a zombie, which still
    # answers a liveness probe. The real caller is never our child.
    reaper = threading.Thread(target=caller.wait)
    reaper.start()
    started = time.monotonic()
    windows_updater.run(_job(tmp_path, wait_pids=[caller.pid]), io.StringIO())
    reaper.join()
    assert 1.0 <= time.monotonic() - started < 60


def test_a_caller_that_never_exits_is_not_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(windows_updater, "_WAIT_SECONDS", 0.2)
    log = io.StringIO()
    windows_updater._wait_for_exit([os.getpid()], windows_updater._Log(log), timeout=0.2)
    assert "still running" in log.getvalue()


def test_main_logs_to_the_job_file(tmp_path: Path) -> None:
    job_file = tmp_path / "job.json"
    job_file.write_text(json.dumps(_job(tmp_path)), encoding="utf-8")
    assert windows_updater.main([str(job_file)]) == 0
    text = (tmp_path / "update.log").read_text(encoding="utf-8")
    assert "Updating Precursor 2026.9.1 → 2026.9.2" in text
    assert "Update installed." in text


def test_the_helper_imports_nothing_from_precursor() -> None:
    """It runs while the package is deleted and rewritten underneath it."""
    source = Path(windows_updater.__file__).read_text(encoding="utf-8")
    assert "import precursor" not in source
    assert "from precursor" not in source
