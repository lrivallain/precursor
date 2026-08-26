"""Login-item installation for the supervised instance.

``precursor service install`` registers Precursor to start when the user logs
in, using whatever the platform's native mechanism is — a launchd LaunchAgent on
macOS, a systemd *user* unit on Linux, a Startup-folder shortcut on Windows.
All three run the same command (``precursor service start --foreground``), so
the supervised process is identical however it was launched.

The unit deliberately runs as the *user*, not as a system daemon: Precursor is a
single-user local app that reads the user's ``gh`` credentials and home
directory, and has no authentication of its own.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from precursor.backend.config import get_settings
from precursor.backend.supervisor import working_dir

LAUNCHD_LABEL = "io.github.lrivallain.precursor"
SYSTEMD_UNIT = "precursor.service"


class AutostartError(RuntimeError):
    """The login item could not be installed or removed."""


@dataclass(frozen=True)
class AutostartInfo:
    """Where the login item lives, and whether it is currently registered."""

    supported: bool
    installed: bool
    kind: str
    path: str | None = None


def _launch_command() -> list[str]:
    """The argv a login item should run.

    Prefers the installed console script, because a launchd agent gets a minimal
    PATH and ``sys.executable`` inside a `uv tool` venv is the most reliable
    absolute entry point we can name.
    """
    script = shutil.which("precursor-ai") or shutil.which("precursor")
    if script:
        return [script, "service", "start", "--foreground"]
    return [sys.executable, "-m", "precursor.backend", "service", "start", "--foreground"]


def _macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _systemd_unit_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "systemd" / "user" / SYSTEMD_UNIT


def _windows_startup_path() -> Path:  # pragma: no cover - Windows-only path
    appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    return (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / "precursor.cmd"
    )


def _target_path() -> Path | None:
    if sys.platform == "darwin":
        return _macos_plist_path()
    if os.name == "nt":  # pragma: no cover - Windows-only path
        return _windows_startup_path()
    if sys.platform.startswith("linux"):
        return _systemd_unit_path()
    return None


def info() -> AutostartInfo:
    path = _target_path()
    if path is None:
        return AutostartInfo(supported=False, installed=False, kind="unsupported")
    kind = {"darwin": "launchd"}.get(
        sys.platform, "systemd" if os.name != "nt" else "startup-folder"
    )
    return AutostartInfo(supported=True, installed=path.is_file(), kind=kind, path=str(path))


def _write_launchd(path: Path) -> None:
    cfg = get_settings()
    logs = Path(cfg.logs_dir)
    logs.mkdir(parents=True, exist_ok=True)
    plist: dict[str, object] = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": _launch_command(),
        "RunAtLoad": True,
        # Restart if it ever exits non-zero, but throttle so a crash loop
        # doesn't spin the CPU.
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "ProcessType": "Interactive",
        "WorkingDirectory": str(working_dir()),
        "StandardOutPath": str(logs / "launchd.out.log"),
        "StandardErrorPath": str(logs / "launchd.err.log"),
        # launchd hands the agent a bare PATH; `precursor service update` and
        # the GitHub token lookup both shell out to tools in the user's prefix.
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        plistlib.dump(plist, handle)


def _write_systemd(path: Path) -> None:
    exec_start = " ".join(_launch_command())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Unit]\n"
        "Description=Precursor\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start}\n"
        f"WorkingDirectory={working_dir()}\n"
        "Restart=on-failure\n"
        "RestartSec=30\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n",
        encoding="utf-8",
    )


def _write_windows(path: Path) -> None:  # pragma: no cover - Windows-only path
    argv = " ".join(f'"{part}"' for part in _launch_command())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'@echo off\r\nstart "" /b {argv}\r\n', encoding="utf-8")


def install() -> AutostartInfo:
    """Register the login item for the current user."""
    path = _target_path()
    if path is None:
        raise AutostartError(f"Autostart is not supported on {sys.platform}.")
    if sys.platform == "darwin":
        _write_launchd(path)
        # `bootstrap` is idempotent-hostile: it errors if already loaded, so
        # unload first and ignore the "not loaded" case.
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
            check=False,
            capture_output=True,
        )
        result = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AutostartError(
                f"launchctl bootstrap failed: {result.stderr.strip() or result.returncode}"
            )
    elif os.name == "nt":  # pragma: no cover - Windows-only path
        _write_windows(path)
    else:
        _write_systemd(path)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, capture_output=True)
        result = subprocess.run(
            ["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AutostartError(
                f"systemctl enable failed: {result.stderr.strip() or result.returncode}"
            )
    return info()


def uninstall() -> AutostartInfo:
    """Remove the login item. Silent when nothing was installed."""
    path = _target_path()
    if path is None:
        raise AutostartError(f"Autostart is not supported on {sys.platform}.")
    if sys.platform == "darwin":
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
            check=False,
            capture_output=True,
        )
    elif os.name != "nt" and path.is_file():
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT],
            check=False,
            capture_output=True,
        )
    if path.is_file():
        path.unlink()
    if os.name != "nt" and sys.platform != "darwin":
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, capture_output=True)
    return info()
