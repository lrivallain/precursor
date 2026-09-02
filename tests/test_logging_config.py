"""The instance log — one file, written by the app itself, in every mode.

`precursor.log` used to exist only because the supervisor redirected a child's
stdout into it. That worked for `precursor service start`, and only for that:
once a launchd agent or a systemd unit owned the process, the service manager
captured stdio somewhere of its own choosing and the file the tray, `service
logs` and `runtime.json` all pointed at simply stopped being written — silently,
and while `service status` still advertised it.

So the writer moved into the process: a rotating handler configured from
:mod:`precursor.backend.logging_config`. These tests pin the parts that made the
old arrangement fail — that the file is written, that it is capped, that it is
not written *twice*, and that a log destination can never keep the app from
booting.
"""

from __future__ import annotations

import logging
import logging.config
from pathlib import Path
from typing import Any

import pytest

from precursor.backend import config
from precursor.backend.logging_config import (
    LOG_FILENAME,
    build_log_config,
    configure_logging,
    log_path,
)


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("PRECURSOR_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()
    # Never leave a test's handlers (or its tmp_path file) attached to the root
    # logger for the next test to write through.
    logging.config.dictConfig(build_log_config("info", color=False))


# --- the file exists at all ---------------------------------------------------


def test_no_log_file_means_console_only() -> None:
    """The default is unchanged: a bare stderr handler."""
    cfg = build_log_config("info", color=False)
    assert cfg["root"]["handlers"] == ["default"]
    assert "file" not in cfg["handlers"]


def test_a_log_file_adds_a_rotating_handler(tmp_path: Path) -> None:
    cfg = build_log_config("info", color=False, log_file=tmp_path / "precursor.log")
    handler = cfg["handlers"]["file"]
    assert handler["class"] == "logging.handlers.RotatingFileHandler"
    assert handler["filename"] == str(tmp_path / "precursor.log")
    # Unrotated is how a service manager's own capture reaches tens of MB.
    assert handler["maxBytes"] > 0
    assert handler["backupCount"] > 0


def test_the_file_is_never_coloured(tmp_path: Path) -> None:
    """ANSI escapes belong on a terminal, not in a file someone greps."""
    cfg = build_log_config("info", color=True, log_file=tmp_path / "precursor.log", console=True)
    assert cfg["formatters"][cfg["handlers"]["file"]["formatter"]]["color"] is False
    assert cfg["formatters"][cfg["handlers"]["default"]["formatter"]]["color"] is True


def test_records_actually_reach_the_file(tmp_path: Path) -> None:
    target = tmp_path / "logs" / "precursor.log"
    configure_logging("info", log_file=target, console=False)
    logging.getLogger("precursor.test").info("hello from the instance")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert "hello from the instance" in target.read_text(encoding="utf-8")


def test_the_parent_directory_is_created(tmp_path: Path) -> None:
    """A fresh install has no logs/ yet, and that must not be an error."""
    target = tmp_path / "brand" / "new" / "precursor.log"
    configure_logging("info", log_file=target, console=False)
    assert target.parent.is_dir()


# --- and only once ------------------------------------------------------------


def test_a_captured_stderr_does_not_get_a_second_copy(tmp_path: Path) -> None:
    """Under launchd/systemd, stderr is already being written to a file.

    Keeping the stream handler there would put every line in two files, one of
    which nothing ever rotates — which is the concrete way this went wrong.
    """
    cfg = build_log_config("info", color=False, log_file=tmp_path / "p.log", console=False)
    assert cfg["root"]["handlers"] == ["file"]


def test_a_terminal_still_gets_both(tmp_path: Path) -> None:
    cfg = build_log_config("info", color=False, log_file=tmp_path / "p.log", console=True)
    assert set(cfg["root"]["handlers"]) == {"default", "file"}


def test_console_follows_the_tty_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stderr.isatty", lambda: True, raising=False)
    assert "default" in build_log_config("info", log_file=tmp_path / "p.log")["root"]["handlers"]
    monkeypatch.setattr("sys.stderr.isatty", lambda: False, raising=False)
    assert build_log_config("info", log_file=tmp_path / "p.log")["root"]["handlers"] == ["file"]


def test_a_process_is_never_left_mute(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no file to write to, the console is kept whatever was asked for."""
    monkeypatch.setattr("sys.stderr.isatty", lambda: False, raising=False)
    assert build_log_config("info", console=False)["root"]["handlers"] == ["default"]


# --- and never at the cost of starting ----------------------------------------


def test_an_unusable_log_file_is_dropped_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full disk or a read-only data dir must not become an outage."""

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "mkdir", _refuse)
    cfg = configure_logging("info", log_file=tmp_path / "nope" / "precursor.log")
    assert cfg["root"]["handlers"] == ["default"]


# --- one path, shared by writer and readers -----------------------------------


def test_the_log_path_follows_the_data_directory(tmp_path: Path) -> None:
    assert log_path() == tmp_path / "logs" / LOG_FILENAME


def test_rotation_limits_come_from_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PRECURSOR_LOG_FILE_MAX_BYTES", "12345")
    monkeypatch.setenv("PRECURSOR_LOG_FILE_BACKUPS", "7")
    config.get_settings.cache_clear()
    cfg = configure_logging("info", log_file=tmp_path / "precursor.log", console=False)
    assert cfg["handlers"]["file"]["maxBytes"] == 12345
    assert cfg["handlers"]["file"]["backupCount"] == 7


# --- the wiring, not just the mechanism ---------------------------------------
#
# The regression was never in `build_log_config`; it was that nothing asked for a
# file. These pin the call sites that make the fix real.


def test_the_production_path_asks_for_the_log_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deleting `log_file=` from `_run_prod` restores the stale-log bug exactly."""
    from precursor.backend import __main__ as entry

    seen: dict[str, object] = {}

    def _capture(_level: str, **kwargs: object) -> dict[str, Any]:
        seen.update(kwargs)
        return {"version": 1, "handlers": {}, "root": {"handlers": []}}

    monkeypatch.setattr(entry, "configure_logging", _capture)
    monkeypatch.setattr(entry, "_ensure_frontend_built", lambda **_k: True)
    monkeypatch.setattr(entry, "_resolve_port", lambda *_a, **_k: 8123)
    monkeypatch.setattr(entry, "_announce_when_ready", lambda **_k: None)
    monkeypatch.setattr(entry.uvicorn, "run", lambda *_a, **_k: None)

    entry._run_prod("127.0.0.1", 8123, "info", strict_port=False, open_browser=False)
    assert seen["log_file"] == log_path()


def test_a_second_instance_leaves_the_log_to_the_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rotating handler assumes one writer, and a busy port auto-bumps rather
    than refusing — so a second instance must not take the first one's log."""
    import os

    from precursor.backend import __main__ as entry
    from precursor.backend import supervisor

    def _state(pid: int) -> supervisor.RuntimeState:
        return supervisor.RuntimeState(
            pid=pid,
            host="127.0.0.1",
            port=8000,
            url="http://127.0.0.1:8000/",
            version="2026.1.0",
            started_at="2026-01-01T00:00:00+00:00",
            log_file=str(log_path()),
        )

    def _running(pid: int):
        return lambda: supervisor.Status(running=True, state=_state(pid))

    monkeypatch.setattr(supervisor, "status", _running(os.getpid() + 1))
    assert entry._owned_log_file() is None

    # `run_foreground` records its own pid before serving, so it keeps the file.
    monkeypatch.setattr(supervisor, "status", _running(os.getpid()))
    assert entry._owned_log_file() == log_path()

    # And a supervised child starts before its parent records anything.
    monkeypatch.setattr(supervisor, "status", lambda: supervisor.Status(running=False, state=None))
    assert entry._owned_log_file() == log_path()


def test_the_login_item_preflight_does_not_open_the_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """`run_foreground` logs before it knows whether it is the instance.

    Opening the file that early would give it two writers in the one case that
    matters — the branch where another instance is already running and holds it.
    """
    from precursor.backend import supervisor

    seen: dict[str, object] = {}

    def _capture(_level: str, **kwargs: object) -> dict[str, Any]:
        seen.update(kwargs)
        return {}

    monkeypatch.setattr("precursor.backend.logging_config.configure_logging", _capture)
    monkeypatch.setattr(
        supervisor,
        "status",
        lambda: supervisor.Status(
            running=True,
            state=supervisor.RuntimeState(
                pid=1,
                host="127.0.0.1",
                port=8000,
                url="http://127.0.0.1:8000/",
                version="2026.1.0",
                started_at="2026-01-01T00:00:00+00:00",
                log_file=str(log_path()),
            ),
        ),
    )
    supervisor.run_foreground()
    assert "log_file" not in seen
