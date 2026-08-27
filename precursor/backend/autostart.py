"""Login-item installation for Precursor's background processes.

``precursor service install`` registers Precursor to start when the user logs
in, using whatever the platform's native mechanism is — a launchd LaunchAgent on
macOS, a systemd *user* unit on Linux, a Startup-folder shortcut on Windows.

There are two things worth starting at login, and they are genuinely separate
processes: the **app** (``service start --foreground``), which serves the UI and
owns the data, and the **tray** (``tray``), which is only a menu-bar control.
Registering them as separate units keeps that separation honest — quitting the
icon must not stop the app, and a machine with no GUI (or without the ``tray``
extra) simply has no tray unit rather than a login item that fails every boot.

The units deliberately run as the *user*, not as a system daemon: Precursor is a
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

from precursor.backend.supervisor import working_dir

LAUNCHD_LABEL = "io.github.lrivallain.precursor"
SYSTEMD_UNIT = "precursor.service"


class AutostartError(RuntimeError):
    """The login item could not be installed or removed."""


@dataclass(frozen=True)
class Unit:
    """One thing that can be registered to start at login."""

    key: str
    # Human-readable, used in CLI output.
    title: str
    # Arguments appended to the resolved console script.
    args: tuple[str, ...]

    @property
    def label(self) -> str:
        """launchd label. The app keeps the original so upgrades don't orphan it."""
        return LAUNCHD_LABEL if self.key == "app" else f"{LAUNCHD_LABEL}.{self.key}"

    @property
    def systemd_name(self) -> str:
        return SYSTEMD_UNIT if self.key == "app" else f"precursor-{self.key}.service"

    @property
    def windows_name(self) -> str:
        return "precursor.cmd" if self.key == "app" else f"precursor-{self.key}.cmd"


APP = Unit(key="app", title="Precursor", args=("service", "start", "--foreground"))
TRAY = Unit(key="tray", title="Precursor tray", args=("tray",))
UNITS = (APP, TRAY)


@dataclass(frozen=True)
class AutostartInfo:
    """Where a login item lives, and whether it is currently registered."""

    unit: str
    supported: bool
    installed: bool
    kind: str
    path: str | None = None

    @property
    def controllable(self) -> bool:
        """Whether the platform can start/stop this unit on demand.

        launchd and systemd are real service managers. A Windows Startup entry
        is just a shortcut executed at login — there is nothing to ask, so the
        supervisor keeps managing that process directly.
        """
        return self.installed and self.kind in ("launchd", "systemd")


def tray_supported() -> bool:
    """Whether registering the tray makes sense on this install.

    The GUI bindings ship behind the ``tray`` extra, so a headless or
    server-side install has none — and a login item that fails on every boot is
    worse than no login item at all.
    """
    from precursor.backend import tray

    return tray.gui_available()


def _launch_command(unit: Unit) -> list[str]:
    """The argv a login item should run.

    Prefers the installed console script, because a launchd agent gets a minimal
    PATH and ``sys.executable`` inside a `uv tool` venv is the most reliable
    absolute entry point we can name.
    """
    script = shutil.which("precursor-ai") or shutil.which("precursor")
    if script:
        return [script, *unit.args]
    return [sys.executable, "-m", "precursor.backend", *unit.args]


def _kind() -> str:
    if sys.platform == "darwin":
        return "launchd"
    if os.name == "nt":  # pragma: no cover - Windows-only path
        return "startup-folder"
    return "systemd"


def _macos_plist_path(unit: Unit) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{unit.label}.plist"


def _systemd_unit_path(unit: Unit) -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "systemd" / "user" / unit.systemd_name


def _windows_startup_path(unit: Unit) -> Path:  # pragma: no cover - Windows-only path
    appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    return (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / unit.windows_name
    )


def _target_path(unit: Unit) -> Path | None:
    if sys.platform == "darwin":
        return _macos_plist_path(unit)
    if os.name == "nt":  # pragma: no cover - Windows-only path
        return _windows_startup_path(unit)
    if sys.platform.startswith("linux"):
        return _systemd_unit_path(unit)
    return None


def info(unit: Unit = APP) -> AutostartInfo:
    path = _target_path(unit)
    if path is None:
        return AutostartInfo(unit=unit.key, supported=False, installed=False, kind="unsupported")
    return AutostartInfo(
        unit=unit.key,
        supported=True,
        installed=path.is_file(),
        kind=_kind(),
        path=str(path),
    )


def info_all() -> list[AutostartInfo]:
    return [info(unit) for unit in UNITS]


def _write_launchd(unit: Unit, path: Path) -> None:
    from precursor.backend.config import get_settings

    logs = Path(get_settings().logs_dir)
    logs.mkdir(parents=True, exist_ok=True)
    plist: dict[str, object] = {
        "Label": unit.label,
        "ProgramArguments": _launch_command(unit),
        "RunAtLoad": True,
        # Restart if it ever exits non-zero, but throttle so a crash loop
        # doesn't spin the CPU. A *clean* exit — the app yielding to an instance
        # that is already serving, or the user picking "Quit tray" — is
        # deliberately not restarted.
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "ProcessType": "Interactive",
        "WorkingDirectory": str(working_dir()),
        "StandardOutPath": str(logs / f"launchd.{unit.key}.out.log"),
        "StandardErrorPath": str(logs / f"launchd.{unit.key}.err.log"),
        # launchd hands the agent a bare PATH; `precursor service update` and
        # the GitHub token lookup both shell out to tools in the user's prefix.
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        plistlib.dump(plist, handle)


def _write_systemd(unit: Unit, path: Path) -> None:
    exec_start = " ".join(_launch_command(unit))
    path.parent.mkdir(parents=True, exist_ok=True)
    # `default.target` rather than `graphical-session.target` even for the tray:
    # not every desktop reaches the latter for user units, and an icon that
    # sometimes never appears is worse than one started slightly early — the
    # tray already tolerates the app not being up yet.
    path.write_text(
        "[Unit]\n"
        f"Description={unit.title}\n"
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


def _write_windows(unit: Unit, path: Path) -> None:  # pragma: no cover - Windows-only path
    argv = " ".join(f'"{part}"' for part in _launch_command(unit))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'@echo off\r\nstart "" /b {argv}\r\n', encoding="utf-8")


def install(unit: Unit = APP) -> AutostartInfo:
    """Register one login item for the current user."""
    path = _target_path(unit)
    if path is None:
        raise AutostartError(f"Autostart is not supported on {sys.platform}.")
    if sys.platform == "darwin":
        _write_launchd(unit, path)
        # `bootstrap` is idempotent-hostile: it errors if already loaded, so
        # unload first and ignore the "not loaded" case.
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/{unit.label}"],
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
                f"launchctl bootstrap failed for {unit.title}: "
                f"{result.stderr.strip() or result.returncode}"
            )
    elif os.name == "nt":  # pragma: no cover - Windows-only path
        _write_windows(unit, path)
    else:
        _write_systemd(unit, path)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, capture_output=True)
        result = subprocess.run(
            ["systemctl", "--user", "enable", "--now", unit.systemd_name],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AutostartError(
                f"systemctl enable failed for {unit.title}: "
                f"{result.stderr.strip() or result.returncode}"
            )
    return info(unit)


def uninstall(unit: Unit = APP) -> AutostartInfo:
    """Remove one login item. Silent when nothing was installed."""
    path = _target_path(unit)
    if path is None:
        raise AutostartError(f"Autostart is not supported on {sys.platform}.")
    if sys.platform == "darwin":
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/{unit.label}"],
            check=False,
            capture_output=True,
        )
    elif os.name != "nt" and path.is_file():
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", unit.systemd_name],
            check=False,
            capture_output=True,
        )
    if path.is_file():
        path.unlink()
    if os.name != "nt" and sys.platform != "darwin":
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, capture_output=True)
    return info(unit)


def uninstall_all() -> list[AutostartInfo]:
    return [uninstall(unit) for unit in UNITS]


# --- controlling an installed unit ------------------------------------------
#
# Once a login item is registered, *it* owns the process: launchd's KeepAlive
# and systemd's Restart= both resurrect a process that merely gets killed. So
# stopping or restarting has to go through the service manager, or the manager
# and the caller end up racing each other for the port.


def _run_unit_cmd(cmd: list[str], action: str, unit: Unit) -> None:
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise AutostartError(
            f"Could not {action} {unit.title}: {result.stderr.strip() or result.returncode}"
        )


def _macos_bootstrap(unit: Unit, action: str) -> None:
    """Load the job into the user's GUI domain, which also starts it (RunAtLoad)."""
    path = _target_path(unit)
    if path is None:
        raise AutostartError(f"Autostart is not supported on {sys.platform}.")
    _run_unit_cmd(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], action, unit)


def start_unit(unit: Unit = APP) -> None:
    """Ask the service manager to start the unit."""
    if sys.platform == "darwin":
        # `kickstart` only works on a job that is already loaded; a job that was
        # booted out (which is how we stop) has to be bootstrapped again.
        probe = subprocess.run(
            ["launchctl", "kickstart", f"gui/{os.getuid()}/{unit.label}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            _macos_bootstrap(unit, "start")
    elif os.name != "nt":
        _run_unit_cmd(["systemctl", "--user", "start", unit.systemd_name], "start", unit)


def stop_unit(unit: Unit = APP) -> None:
    """Ask the service manager to stop the unit, and to stay stopped.

    On launchd that means booting the job out rather than signalling it: a
    plain kill is exactly what KeepAlive exists to undo. The plist stays on
    disk, so it is loaded again at the next login.
    """
    if sys.platform == "darwin":
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/{unit.label}"],
            check=False,
            capture_output=True,
        )
    elif os.name != "nt":
        subprocess.run(
            ["systemctl", "--user", "stop", unit.systemd_name], check=False, capture_output=True
        )


def restart_unit(unit: Unit = APP) -> None:
    """Restart the unit in one operation, leaving no window for a race."""
    if sys.platform == "darwin":
        # -k kills the running instance and starts a new one atomically, so
        # nothing else can claim the port in between. It still needs a *loaded*
        # job, though: a plist on disk is not the same as a job launchd knows
        # about, and the two diverge whenever the instance was stopped or its
        # executable was replaced underneath it.
        probe = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{unit.label}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            _macos_bootstrap(unit, "restart")
    elif os.name != "nt":
        _run_unit_cmd(["systemctl", "--user", "restart", unit.systemd_name], "restart", unit)
