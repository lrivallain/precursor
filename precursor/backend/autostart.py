"""Login-item installation for Precursor's background processes.

``precursor service install`` registers Precursor to start when the user logs
in, using whatever the platform's native mechanism is — a launchd LaunchAgent on
macOS, a systemd *user* unit on Linux, a per-user ``Run`` registry entry on
Windows.

There are two things worth starting at login, and they are genuinely separate
processes: the **app** (``service start --foreground``), which serves the UI and
owns the data, and the **tray** (``tray``), which is only a menu-bar control.
Registering them as separate units keeps that separation honest — quitting the
icon must not stop the app, and a machine with no GUI (or without the ``tray``
extra) simply has no tray unit rather than a login item that fails every boot.

The units deliberately run as the *user*, not as a system daemon: Precursor is a
single-user local app that reads the user's ``gh`` credentials and home
directory, and has no authentication of its own.

Windows has no user-level service manager to hand the process to, so its entry
is a launcher rather than a supervisor: at login it runs ``service start`` —
which starts the instance detached, exactly as typing it would — and exits.
Both entries run ``pythonw``, because anything console-based leaves a terminal
window on screen for as long as it runs (the old Startup-folder ``.cmd`` did
precisely that), and closing that window killed Precursor.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from precursor.backend import winproc
from precursor.backend.supervisor import working_dir

LAUNCHD_LABEL = "io.github.lrivallain.precursor"
SYSTEMD_UNIT = "precursor.service"
# The per-user key Explorer runs at login — the same list Task Manager's
# "Startup apps" shows and lets the user switch off.
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# `launchctl bootout` signals the job and returns; it does not wait for the
# process to die. Precursor's own shutdown is a graceful uvicorn one and takes a
# couple of seconds, so bootstrapping the same label immediately afterwards
# lands while launchd still has the old job — which it reports as the opaque
# "Bootstrap failed: 5: Input/output error", *and* leaves nothing registered.
# Re-running `precursor service install` over a running login item therefore
# used to uninstall it. So: wait for launchd to forget the label first.
_UNLOAD_TIMEOUT_SECONDS = 20.0
_UNLOAD_POLL_SECONDS = 0.25


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
        """The Startup-folder script earlier releases wrote; now only cleaned up."""
        return "precursor.cmd" if self.key == "app" else f"precursor-{self.key}.cmd"

    @property
    def windows_value(self) -> str:
        """Name of the value under :data:`WINDOWS_RUN_KEY`."""
        return "Precursor" if self.key == "app" else f"Precursor {self.key.capitalize()}"

    @property
    def windows_args(self) -> tuple[str, ...]:
        # Detached, not --foreground: see the module docstring.
        return ("service", "start") if self.key == "app" else self.args


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

        launchd and systemd are real service managers. A Windows Run entry is
        just a command executed at login — there is nothing to ask, so the
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


def windows_command(unit: Unit) -> list[str]:
    """The argv a Windows Run entry executes: this environment's ``pythonw``.

    Not the ``precursor.exe`` console script: a console program started at login
    gets a console *window*, which stays open for as long as it runs. Looked up
    beside the running interpreter, because a ``uv tool`` venv keeps both — and
    ``uv tool install --force`` recreates it at the same path, so the entry
    survives updates.
    """
    interpreter = Path(sys.executable)
    windowless = interpreter.with_name("pythonw.exe")
    if windowless.is_file():
        interpreter = windowless
    return [str(interpreter), "-m", "precursor.backend", *unit.windows_args]


def _kind() -> str:
    if sys.platform == "darwin":
        return "launchd"
    if os.name == "nt":
        return "registry"
    return "systemd"


def _macos_plist_path(unit: Unit) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{unit.label}.plist"


def _systemd_unit_path(unit: Unit) -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "systemd" / "user" / unit.systemd_name


def _windows_startup_path(unit: Unit) -> Path:
    """Where releases before the Run entry put a ``.cmd`` — removed on sight."""
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
    """The file behind a login item (on Windows, the legacy Startup script).

    Every file-backed lookup goes through here, which is what lets the test
    suite redirect them all at once; the Windows registry equivalent is
    :func:`_run_key`.
    """
    if sys.platform == "darwin":
        return _macos_plist_path(unit)
    if os.name == "nt":
        return _windows_startup_path(unit)
    if sys.platform.startswith("linux"):
        return _systemd_unit_path(unit)
    return None


def _run_key() -> str:
    """The registry key holding Windows Run entries (redirected by the tests)."""
    return WINDOWS_RUN_KEY


def _winreg() -> Any:  # pragma: no cover - Windows-only path
    import winreg

    return winreg


def _run_value(unit: Unit) -> str | None:  # pragma: no cover - Windows-only path
    reg = _winreg()
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, _run_key()) as key:
            value, _type = reg.QueryValueEx(key, unit.windows_value)
    except OSError:
        return None
    return str(value)


def _set_run_value(unit: Unit, command: str) -> None:  # pragma: no cover - Windows-only path
    reg = _winreg()
    with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, _run_key(), 0, reg.KEY_SET_VALUE) as key:
        reg.SetValueEx(key, unit.windows_value, 0, reg.REG_SZ, command)


def _delete_run_value(unit: Unit) -> None:  # pragma: no cover - Windows-only path
    reg = _winreg()
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, _run_key(), 0, reg.KEY_SET_VALUE) as key:
            reg.DeleteValue(key, unit.windows_value)
    except FileNotFoundError:
        pass


def _windows_info(unit: Unit) -> AutostartInfo:  # pragma: no cover - Windows-only path
    legacy = _target_path(unit)
    installed = _run_value(unit) is not None or (legacy is not None and legacy.is_file())
    return AutostartInfo(
        unit=unit.key,
        supported=True,
        installed=installed,
        kind="registry",
        path=f"HKCU\\{_run_key()}\\{unit.windows_value}",
    )


def launch(unit: Unit) -> None:
    """Start a registered unit now, the way the login item will at next login.

    Only meaningful where registering does not already start it — a Windows Run
    entry fires at the next login, whereas launchd (RunAtLoad) and systemd
    (``--now``) start the unit as part of registering it.
    """
    subprocess.Popen(
        windows_command(unit) if os.name == "nt" else _launch_command(unit),
        cwd=str(working_dir()),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **winproc.detached(),
    )


def info(unit: Unit = APP) -> AutostartInfo:
    if _kind() == "registry":  # pragma: no cover - Windows-only path
        return _windows_info(unit)
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


def windows_command_line(unit: Unit) -> str:
    """:func:`windows_command` as the single string a Run entry holds.

    The executable is quoted even without a space in it, as installers write
    them: an unquoted path is re-split by Explorer at every space.
    """
    executable, *arguments = windows_command(unit)
    return f'"{executable}" {subprocess.list2cmdline(arguments)}'


def _install_windows(unit: Unit) -> None:  # pragma: no cover - Windows-only path
    _set_run_value(unit, windows_command_line(unit))
    # Supersedes the Startup-folder script, which would otherwise start a second
    # copy — in a console window — at the next login.
    legacy = _target_path(unit)
    if legacy is not None and legacy.is_file():
        legacy.unlink()


def _macos_loaded(label: str) -> bool:
    """Whether launchd still knows the label in the user's GUI domain."""
    return (
        subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )


def _macos_bootout(unit: Unit, *, timeout: float | None = None) -> None:
    """Unload the job, then wait for launchd to actually let go of the label.

    `bootout` returns once it has *asked* the job to stop, which for a graceful
    uvicorn shutdown is seconds before the process is gone. Anything that
    bootstraps the same label in that window gets EIO, so the wait is what makes
    unload-then-load safe.

    A job that outlives the timeout is left to `bootstrap` to complain about:
    guessing at a failure here would replace launchd's own diagnosis with ours.
    """
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}/{unit.label}"],
        check=False,
        capture_output=True,
    )
    # Read at call time rather than as a default, so the budget stays one
    # patchable number instead of a value frozen at import.
    budget = _UNLOAD_TIMEOUT_SECONDS if timeout is None else timeout
    deadline = time.monotonic() + budget
    while _macos_loaded(unit.label) and time.monotonic() < deadline:
        time.sleep(_UNLOAD_POLL_SECONDS)


def install(unit: Unit = APP) -> AutostartInfo:
    """Register one login item for the current user."""
    path = _target_path(unit)
    if path is None:
        raise AutostartError(f"Autostart is not supported on {sys.platform}.")
    if sys.platform == "darwin":
        _write_launchd(unit, path)
        # `bootstrap` is idempotent-hostile: it errors if already loaded, so
        # unload first — and wait for it, or the two race (see _macos_bootout).
        _macos_bootout(unit)
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
        _install_windows(unit)
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
        # Waited on, so the plist is only removed once the job behind it is
        # really gone — otherwise an uninstall-then-install leaves the same race
        # as a bare re-install.
        _macos_bootout(unit)
    elif os.name == "nt":  # pragma: no cover - Windows-only path
        _delete_run_value(unit)
    elif path.is_file():
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

    Waited on, so "stopped" means the port is actually free — a caller that
    stops and then starts (``supervisor.restart``) would otherwise hand the new
    process a port the old one has not released.
    """
    if sys.platform == "darwin":
        _macos_bootout(unit)
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
