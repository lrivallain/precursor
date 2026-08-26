"""``precursor tray`` — a menu-bar / system-tray control for the instance.

The tray owns no state of its own. It polls the supervisor (which reads
``runtime.json``) for "running or not", and the update service for "is there
something newer", then exposes the four things worth a click: open, start/stop,
update, quit. Everything it does is something ``precursor service …`` can do
from a shell, so the tray stays a convenience and never becomes the only way to
drive the app.

Ships behind the ``tray`` extra (``pip install "precursor-ai[tray]"``) because
``pystray``/``Pillow`` pull in GUI bindings that a headless server install has
no use for.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import logging
import sys
import threading
import webbrowser
from collections.abc import Sequence
from typing import Any

from precursor.backend import supervisor
from precursor.backend.services import updates

logger = logging.getLogger(__name__)

# How often the icon re-reads the supervisor state. Cheap (a file read plus a
# pid probe), so it can be brisk enough to feel live after a manual CLI action.
_POLL_SECONDS = 3.0
# Update checks are network calls, and the service caches them anyway; this is
# just how often we ask it to re-evaluate.
_UPDATE_POLL_SECONDS = 1800.0

_ICON_SIZE = 64

# The GUI bindings live behind the `tray` extra, so they are reached through
# importlib rather than a module-level import — mirroring how the optional
# Copilot SDK is handled in services/agents/runtime.py. Everything in this
# module stays importable (and testable) without them installed.
_GUI_MODULES = ("pystray", "PIL")


def gui_available() -> bool:
    try:
        return all(importlib.util.find_spec(name) is not None for name in _GUI_MODULES)
    except (ImportError, ValueError):  # pragma: no cover - defensive
        return False


def _pystray() -> Any:
    return importlib.import_module("pystray")


def _missing_deps_message() -> str:
    return (
        "The tray needs the optional GUI dependencies.\n"
        'Install them with:  uv tool install --force "precursor-ai[kanban,tray]"\n'
        "  (or, in a checkout:  uv sync --extra tray)"
    )


def _make_image(running: bool) -> Any:
    """A filled dot for running, a hollow ring for stopped.

    Drawn rather than shipped as an asset so the icon has no binary file to keep
    in sync with the theme, and reads correctly on both light and dark menu bars.
    """
    image_mod = importlib.import_module("PIL.Image")
    draw_mod = importlib.import_module("PIL.ImageDraw")

    image = image_mod.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    draw = draw_mod.Draw(image)
    # A neutral mid grey reads on both light and dark menu bars without needing
    # per-theme assets; green is reserved for "actually serving".
    colour = (46, 160, 67, 255) if running else (140, 140, 140, 255)
    box = (8, 8, _ICON_SIZE - 8, _ICON_SIZE - 8)
    if running:
        draw.ellipse(box, fill=colour)
    else:
        draw.ellipse(box, outline=colour, width=6)
    return image


class TrayApp:
    def __init__(self, *, check_updates: bool = True) -> None:
        self._check_updates = check_updates
        self._stop = threading.Event()
        self._status = supervisor.status()
        self._update: updates.UpdateInfo | None = None
        self._busy: str | None = None
        self._icon: Any | None = None

    # --- state -----------------------------------------------------------

    @property
    def _running(self) -> bool:
        return self._status.running

    def _refresh(self) -> None:
        self._status = supervisor.status()
        if self._icon is not None:
            self._icon.icon = _make_image(self._running)
            self._icon.title = self._title()
            self._icon.update_menu()

    def _title(self) -> str:
        if self._busy:
            return f"Precursor — {self._busy}"
        if self._status.running and self._status.state is not None:
            return f"Precursor — running on :{self._status.state.port}"
        return "Precursor — stopped"

    # --- actions ---------------------------------------------------------

    def _in_background(self, label: str, fn: Any) -> None:
        """Run a slow action off the UI thread, keeping the menu responsive."""

        def _run() -> None:
            self._busy = label
            self._refresh()
            try:
                fn()
            except Exception as exc:
                logger.error("%s failed: %s", label, exc)
                self._notify(f"{label} failed", str(exc))
            finally:
                self._busy = None
                self._refresh()

        threading.Thread(target=_run, daemon=True).start()

    def _notify(self, title: str, message: str) -> None:
        icon = self._icon
        if icon is None:
            return
        try:
            icon.notify(message, title)
        except Exception:  # pragma: no cover - notifications are best-effort
            logger.debug("Tray notification unavailable")

    def _open(self, *_: object) -> None:
        state = self._status.state
        if state is not None:
            webbrowser.open(state.url)

    def _start(self, *_: object) -> None:
        self._in_background("starting", supervisor.start)

    def _stop_instance(self, *_: object) -> None:
        self._in_background("stopping", supervisor.stop)

    def _restart(self, *_: object) -> None:
        self._in_background("restarting", supervisor.restart)

    def _apply_update(self, *_: object) -> None:
        def _run() -> None:
            info = updates.check(force=True)
            # Read the live state rather than the polled copy: the decision to
            # bounce the instance shouldn't hinge on a snapshot up to a poll
            # interval old.
            was_running = supervisor.status().running
            summary = updates.apply(info)
            self._update = updates.check(force=True)
            if was_running:
                supervisor.restart()
            self._notify("Precursor updated", summary)

        self._in_background("updating", _run)

    def _check_now(self, *_: object) -> None:
        def _run() -> None:
            self._update = updates.check(force=True)
            info = self._update
            if info.error:
                self._notify("Update check failed", info.error)
            elif info.update_available:
                self._notify("Update available", info.latest_version or "A newer build is ready.")
            else:
                self._notify("Up to date", info.current_version)

        self._in_background("checking for updates", _run)

    def _quit(self, *_: object) -> None:
        self._stop.set()
        if self._icon is not None:
            self._icon.stop()

    # --- menu ------------------------------------------------------------

    def _update_label(self) -> str:
        info = self._update
        if info is None:
            return "Check for updates"
        if info.error:
            return "Check for updates (last check failed)"
        if info.update_available:
            return f"Update to {info.latest_version or 'latest'} and restart"
        return "Check for updates"

    def _build_menu(self) -> Any:
        pystray = _pystray()

        return pystray.Menu(
            pystray.MenuItem(lambda _: self._title(), lambda *_: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Open Precursor",
                self._open,
                default=True,
                enabled=lambda _: self._running and self._busy is None,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start",
                self._start,
                visible=lambda _: not self._running,
                enabled=lambda _: self._busy is None,
            ),
            pystray.MenuItem(
                "Stop",
                self._stop_instance,
                visible=lambda _: self._running,
                enabled=lambda _: self._busy is None,
            ),
            pystray.MenuItem(
                "Restart",
                self._restart,
                visible=lambda _: self._running,
                enabled=lambda _: self._busy is None,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda _: self._update_label(),
                lambda *_: (
                    self._apply_update()
                    if (self._update and self._update.update_available)
                    else self._check_now()
                ),
                enabled=lambda _: self._busy is None,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit tray", self._quit),
        )

    # --- loop ------------------------------------------------------------

    def _poll_loop(self) -> None:
        elapsed = 0.0
        while not self._stop.wait(_POLL_SECONDS):
            self._refresh()
            elapsed += _POLL_SECONDS
            if self._check_updates and elapsed >= _UPDATE_POLL_SECONDS:
                elapsed = 0.0
                try:
                    self._update = updates.check()
                except Exception as exc:  # pragma: no cover - network dependent
                    logger.debug("Background update check failed: %s", exc)

    def run(self) -> int:
        pystray = _pystray()

        self._icon = pystray.Icon(
            "precursor",
            icon=_make_image(self._running),
            title=self._title(),
            menu=self._build_menu(),
        )
        threading.Thread(target=self._poll_loop, daemon=True).start()
        if self._check_updates:
            self._in_background("checking for updates", self._refresh_update_info)
        self._icon.run()
        return 0

    def _refresh_update_info(self) -> None:
        self._update = updates.check()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="precursor tray", description="Menu-bar control for a background Precursor instance."
    )
    parser.add_argument(
        "--start",
        action="store_true",
        help="Start the instance immediately if it isn't already running.",
    )
    parser.add_argument(
        "--no-update-check",
        action="store_true",
        help="Never contact GitHub to look for newer builds.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not gui_available():
        print(_missing_deps_message(), file=sys.stderr)
        return 1

    if args.start and not supervisor.status().running:
        try:
            supervisor.start()
        except supervisor.SupervisorError as exc:
            print(f"error: {exc}", file=sys.stderr)

    return TrayApp(check_updates=not args.no_update_check).run()
