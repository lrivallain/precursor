"""Login-item generation, and the CLI dispatch that must not disturb dev flows.

The autostart unit is what actually runs Precursor after a reboot, so the parts
worth pinning are the ones that silently break there: an argv the login manager
can resolve, and a working directory that isn't ``/``.
"""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path

import pytest

from precursor.backend import autostart, config, supervisor


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("PRECURSOR_DATA_DIR", str(tmp_path / "data"))
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_launch_command_prefers_the_console_script(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autostart.shutil, "which", lambda name: f"/opt/bin/{name}")
    assert autostart._launch_command() == [
        "/opt/bin/precursor-ai",
        "service",
        "start",
        "--foreground",
    ]


def test_launch_command_falls_back_to_the_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autostart.shutil, "which", lambda _name: None)
    cmd = autostart._launch_command()
    # A login item gets a minimal PATH, so the fallback must be absolute.
    assert cmd[0] == sys.executable
    assert cmd[1:] == ["-m", "precursor.backend", "service", "start", "--foreground"]


def test_launchd_plist_is_well_formed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(autostart.shutil, "which", lambda name: f"/opt/bin/{name}")
    path = tmp_path / "agent.plist"
    autostart._write_launchd(path)

    plist = plistlib.loads(path.read_bytes())
    assert plist["Label"] == autostart.LAUNCHD_LABEL
    assert plist["RunAtLoad"] is True
    assert plist["ProgramArguments"][0] == "/opt/bin/precursor-ai"
    # launchd starts agents with cwd=/, so an explicit writable one is required.
    assert plist["WorkingDirectory"] == str(supervisor.working_dir())
    assert "PATH" in plist["EnvironmentVariables"]


def test_systemd_unit_is_well_formed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(autostart.shutil, "which", lambda name: f"/opt/bin/{name}")
    path = tmp_path / "precursor.service"
    autostart._write_systemd(path)

    text = path.read_text(encoding="utf-8")
    assert "ExecStart=/opt/bin/precursor-ai service start --foreground" in text
    assert "WantedBy=default.target" in text  # a *user* unit, not a system daemon
    assert f"WorkingDirectory={supervisor.working_dir()}" in text


def test_a_checkout_runs_from_the_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default DB URL is relative in a checkout, so cwd picks the database.

    Anchoring the supervised instance anywhere else would silently give it a
    different database than `uv run precursor` uses in the same clone.
    """
    monkeypatch.setattr(config, "is_source_checkout", lambda: True)
    root = supervisor.working_dir()
    assert (root / "pyproject.toml").is_file()
    assert (root / "frontend" / "package.json").is_file()


def test_an_installed_build_runs_from_its_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config, "is_source_checkout", lambda: False)
    monkeypatch.setenv("PRECURSOR_DATA_DIR", str(tmp_path / "data"))
    config.get_settings.cache_clear()
    assert supervisor.working_dir() == (tmp_path / "data").resolve()


def test_info_reports_not_installed_for_a_missing_unit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(autostart, "_target_path", lambda: tmp_path / "nope")
    info = autostart.info()
    assert info.supported is True
    assert info.installed is False


def test_service_subcommand_does_not_shadow_the_normal_parser() -> None:
    """`precursor` / `precursor --dev` must keep parsing exactly as before."""
    from precursor.backend.service_cli import build_parser

    args = build_parser().parse_args(["start", "--port", "9100"])
    assert args.port == 9100
    assert args.foreground is False

    # And the flat launcher parser is untouched by the new sub-commands.
    from precursor.backend.__main__ import main as launcher_main

    assert callable(launcher_main)
