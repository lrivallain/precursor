"""A windowless launch must not die before it can say why.

``pythonw`` — what the Windows login items and the tray run under — hands the
process ``None`` for ``sys.stdout``/``sys.stderr``. The logging setup calls
``sys.stderr.isatty()`` and the banner prints to stderr, so without a stand-in
the process crashed on startup, silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from precursor.backend import __main__ as launcher
from precursor.backend import config


@pytest.fixture
def _logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("PRECURSOR_DATA_DIR", str(tmp_path / "data"))
    config.get_settings.cache_clear()
    yield (tmp_path / "data").resolve() / "logs"
    config.get_settings.cache_clear()


def test_missing_streams_are_captured_to_a_file(
    monkeypatch: pytest.MonkeyPatch, _logs: Path
) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "argv", ["precursor", "tray"])

    launcher._ensure_stdio()
    try:
        assert sys.stderr is not None and sys.stdout is not None
        assert sys.stderr.isatty() is False
        print("▶ no crash", file=sys.stderr)
        sys.stderr.flush()
        assert "▶ no crash" in (_logs / "windows.tray.out.log").read_text(encoding="utf-8")
    finally:
        sys.stderr.close()


def test_real_streams_are_left_alone(monkeypatch: pytest.MonkeyPatch, _logs: Path) -> None:
    stdout, stderr = sys.stdout, sys.stderr
    launcher._ensure_stdio()
    assert (sys.stdout, sys.stderr) == (stdout, stderr)
    assert not _logs.exists()
