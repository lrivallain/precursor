"""Handing the data directory to the desktop file manager.

The reveal path is thin, but it is the one place a user reaches the database and
the logs *when the app won't start* — so it must not depend on a running
instance, and it must say something useful when the platform has no file
manager to hand off to.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from precursor.backend import desktop


def _record(monkeypatch: pytest.MonkeyPatch, *, returncode: int = 0) -> list[list[str]]:
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="boom")

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    return seen


def test_reveal_invokes_the_platform_opener(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(desktop, "_opener", lambda: ["open"])
    seen = _record(monkeypatch)
    desktop.reveal(tmp_path)
    assert seen == [["open", str(tmp_path)]]


def test_reveal_creates_a_missing_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fresh install hasn't written the data dir yet, and an empty folder is a
    better answer than a file manager error."""
    monkeypatch.setattr(desktop, "_opener", lambda: ["open"])
    _record(monkeypatch)
    target = tmp_path / "not" / "there" / "yet"
    desktop.reveal(target)
    assert target.is_dir()


def test_a_failing_opener_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(desktop, "_opener", lambda: ["xdg-open"])
    monkeypatch.setattr(desktop.os, "name", "posix")
    _record(monkeypatch, returncode=3)
    with pytest.raises(desktop.RevealError, match="boom"):
        desktop.reveal(tmp_path)


def test_explorer_exit_code_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Explorer returns 1 even when it opened the window, so its code says
    nothing and treating it as failure would break the common case."""
    monkeypatch.setattr(desktop, "_opener", lambda: ["explorer"])
    monkeypatch.setattr(desktop.os, "name", "nt")
    _record(monkeypatch, returncode=1)
    desktop.reveal(tmp_path)  # must not raise


def test_no_file_manager_says_so_and_names_the_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(desktop, "_opener", lambda: None)
    with pytest.raises(desktop.RevealError, match=str(tmp_path)):
        desktop.reveal(tmp_path)


def test_a_missing_opener_binary_is_not_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(desktop, "_opener", lambda: ["open"])

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise FileNotFoundError("open")

    monkeypatch.setattr(desktop.subprocess, "run", boom)
    with pytest.raises(desktop.RevealError):
        desktop.reveal(tmp_path)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS opener")
def test_macos_uses_open() -> None:
    assert desktop._opener() == ["open"]


def test_linux_without_xdg_open_is_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(desktop.os, "name", "posix")
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: None)
    assert desktop._opener() is None


def test_service_cli_exposes_the_same_action() -> None:
    """The tray must never be the only way to reach something."""
    from precursor.backend.service_cli import build_parser

    args = build_parser().parse_args(["data-dir", "--reveal"])
    assert args.reveal is True
    assert build_parser().parse_args(["data-dir"]).reveal is False
