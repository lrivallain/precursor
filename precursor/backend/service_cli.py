"""``precursor service …`` — manage a background instance.

Split out of :mod:`precursor.backend.__main__` so the plain ``precursor`` /
``precursor --dev`` argument parsing stays exactly as it was: development
workflows (auto-bumped ports, one instance per worktree) are unaffected by
anything here.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence

from precursor.backend import autostart, desktop, supervisor
from precursor.backend.services import updates


def _print_status(status: supervisor.Status, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(status.as_dict(), indent=2))
        return
    if not status.running or status.state is None:
        suffix = " (stale state file cleared)" if status.stale else ""
        print(f"Precursor is not running{suffix}.")
        return
    state = status.state
    print("Precursor is running.")
    print(f"  URL      {state.url}")
    print(f"  PID      {state.pid}")
    print(f"  Version  {state.version}")
    print(f"  Since    {state.started_at}")
    print(f"  Log      {state.log_file}")


def _cmd_start(args: argparse.Namespace) -> int:
    if args.foreground:
        supervisor.run_foreground(host=args.host, port=args.port, log_level=args.log_level)
        return 0
    before = supervisor.status()
    status = supervisor.start(host=args.host, port=args.port, log_level=args.log_level)
    if before.running:
        print("Precursor is already running.")
    _print_status(status, as_json=False)
    return 0


def _cmd_stop(_args: argparse.Namespace) -> int:
    if supervisor.stop():
        print("Precursor stopped.")
    else:
        print("Precursor was not running.")
    return 0


def _cmd_restart(args: argparse.Namespace) -> int:
    status = supervisor.restart(host=args.host, port=args.port, log_level=args.log_level)
    _print_status(status, as_json=False)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    status = supervisor.status()
    _print_status(status, as_json=args.json)
    return 0 if status.running else 1


def _cmd_install(args: argparse.Namespace) -> int:
    # Before the login item exists, so the unit it registers starts on a port it
    # can actually bind. launchd/systemd start the job as part of registering it,
    # and a busy port would otherwise leave them retrying a doomed bind forever.
    reservation = supervisor.reserve_port(port=args.port)
    if reservation.moved_from is not None:
        print(
            f"Port {reservation.moved_from} is in use — Precursor will run on "
            f"{reservation.port} instead."
        )
    if reservation.env_file:
        print(f"Port {reservation.port} saved to {reservation.env_file}")

    info = autostart.install(autostart.APP)
    print(f"Autostart installed ({info.kind}): {info.path}")

    # The tray is a second, independent login item — the icon coming back at
    # login is what people expect, but it only makes sense where the GUI extra
    # actually resolved.
    if args.tray:
        if autostart.tray_supported():
            tray_info = autostart.install(autostart.TRAY)
            print(f"Tray autostart installed: {tray_info.path}")
        else:
            print("Tray autostart skipped — the `tray` extra is not installed.")

    # launchd (RunAtLoad) and systemd (--now) start the instance as part of
    # registering it, so give the unit a moment to come up before deciding
    # whether anything is still missing. Starting one here unconditionally
    # would race the unit and lose to --strict-port.
    for _ in range(20):
        if supervisor.status().running:
            break
        time.sleep(0.5)
    status = supervisor.status()
    if not status.running:
        status = supervisor.start()
    _print_status(status, as_json=False)
    return 0


def _cmd_uninstall(_args: argparse.Namespace) -> int:
    for info in autostart.uninstall_all():
        print(f"Autostart removed ({info.kind}): {info.unit}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    info = updates.check(force=True)
    if args.json:
        print(json.dumps(info.as_dict(), indent=2))
        return 0
    if info.error:
        print(f"Could not check for updates: {info.error}")
        return 1
    print(f"Channel  {info.channel} ({info.install_mode} install)")
    print(f"Current  {info.current_version}")
    print(f"Latest   {info.latest_version or 'unknown'}")
    print("Update available." if info.update_available else "Up to date.")
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    info = updates.check(force=True)
    if info.error and not args.force:
        print(f"Could not check for updates: {info.error}")
        return 1
    if not info.update_available and not args.force:
        print(f"Already on the latest {info.channel} build ({info.current_version}).")
        return 0
    print(f"Updating {info.current_version} → {info.latest_version or 'latest'}…")
    print(updates.apply(info))
    was_running = supervisor.status().running
    if was_running:
        # Restart so the new code is actually serving; the child re-execs from
        # the freshly installed interpreter environment.
        _print_status(supervisor.restart(), as_json=False)
    else:
        print("Precursor is not running — start it with `precursor service start`.")
    _restart_tray_after_update()
    return 0


def _restart_tray_after_update() -> None:
    """Bounce the menu-bar icon so it isn't left running the previous release.

    The tray is a separate long-lived process: updating the wheel replaces the
    code on disk but leaves the running icon executing whatever it imported at
    startup. That is not cosmetic — the tray's own menu drives the supervisor,
    so a stale icon keeps offering the *previous* version's behaviour long after
    the app itself has moved on.
    """
    info = autostart.info(autostart.TRAY)
    if not info.controllable:
        # Not registered as a unit: either it isn't running, or the user starts
        # it by hand and owns its lifecycle.
        return
    try:
        autostart.restart_unit(autostart.TRAY)
        print("Menu-bar icon restarted.")
    except autostart.AutostartError as exc:
        # Never fail an otherwise-successful update over the icon.
        print(f"note: could not restart the menu-bar icon ({exc}). Restart it when convenient.")


def _cmd_data_dir(args: argparse.Namespace) -> int:
    from pathlib import Path

    from precursor.backend.config import get_settings

    path = Path(get_settings().data_dir).resolve()
    if args.reveal:
        desktop.reveal(path)
        return 0
    # Bare path on stdout so it composes: `cd "$(precursor service data-dir)"`.
    print(path)
    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    from pathlib import Path

    from precursor.backend.config import get_settings

    log_file = Path(get_settings().logs_dir) / "precursor.log"
    if not log_file.is_file():
        print(f"No log file yet at {log_file}.")
        return 1
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-args.lines :]:
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="precursor service",
        description="Manage a background Precursor instance.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def _with_run_args(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--host", default=None, help="Bind host (defaults to the configured host).")
        p.add_argument(
            "--port",
            type=int,
            default=None,
            help="Port to serve on (defaults to the configured port).",
        )
        p.add_argument("--log-level", default=None, help="uvicorn log level.")
        return p

    start = _with_run_args(sub.add_parser("start", help="Start a background instance."))
    start.add_argument(
        "--foreground",
        action="store_true",
        help="Run in this process instead of detaching (used by the login item).",
    )
    start.set_defaults(func=_cmd_start)

    sub.add_parser("stop", help="Stop the background instance.").set_defaults(func=_cmd_stop)

    _with_run_args(sub.add_parser("restart", help="Restart the background instance.")).set_defaults(
        func=_cmd_restart
    )

    status = sub.add_parser("status", help="Show whether an instance is running.")
    status.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    status.set_defaults(func=_cmd_status)

    install = sub.add_parser(
        "install", help="Start Precursor at login (launchd/systemd/Startup) and start it now."
    )
    install.add_argument(
        "--no-tray",
        dest="tray",
        action="store_false",
        help="Register only the app, not the menu-bar icon.",
    )
    install.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to run on. Defaults to the configured one, moving to the next "
        "free port (saved to .env) if it is busy.",
    )
    install.set_defaults(func=_cmd_install, tray=True)

    sub.add_parser("uninstall", help="Remove the login items.").set_defaults(func=_cmd_uninstall)

    check = sub.add_parser("check", help="Check whether a newer build is available.")
    check.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    check.set_defaults(func=_cmd_check)

    update = sub.add_parser("update", help="Update to the latest build and restart.")
    update.add_argument(
        "--force", action="store_true", help="Reinstall even if no update was detected."
    )
    update.set_defaults(func=_cmd_update)

    logs = sub.add_parser("logs", help="Print the tail of the background instance log.")
    logs.add_argument("-n", "--lines", type=int, default=50, help="How many lines to show.")
    logs.set_defaults(func=_cmd_logs)

    data_dir = sub.add_parser("data-dir", help="Print the data directory (database, blobs, logs).")
    data_dir.add_argument(
        "--reveal",
        action="store_true",
        help="Open it in the file manager instead of printing the path.",
    )
    data_dir.set_defaults(func=_cmd_data_dir)

    return parser


def main(argv: Sequence[str]) -> int:
    args = build_parser().parse_args(list(argv))
    try:
        result: int = args.func(args)
        return result
    except (
        supervisor.SupervisorError,
        autostart.AutostartError,
        updates.UpdateError,
        desktop.RevealError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
