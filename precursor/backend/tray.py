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
import os
import sys
import threading
import time
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# The release *this process* is executing. `precursor.__version__` is resolved
# once, at import, so a long-lived tray keeps measuring against the build it was
# started with — which is exactly what makes it the thing to compare the running
# instance against. Aliased so the staleness check reads as a deliberate
# snapshot rather than a live lookup.
from precursor import __version__ as _OWN_VERSION
from precursor.backend import desktop, supervisor
from precursor.backend.config import get_settings
from precursor.backend.services import updates

logger = logging.getLogger(__name__)

# How often the icon re-reads the supervisor state. Cheap (a file read plus a
# pid probe), so it can be brisk enough to feel live after a manual CLI action.
_POLL_SECONDS = 3.0
# Update checks are network calls, and the service caches them anyway; this is
# just how often we ask it to re-evaluate.
_UPDATE_POLL_SECONDS = 1800.0

_ICON_SIZE = 64
# The logo is drawn at a multiple of the target size and downsampled, because
# Pillow's shape primitives are not antialiased — at 1x the bubble's rounded
# corners and the tail's diagonal come out visibly jagged.
_SUPERSAMPLE = 8

# Precursor's mark, transcribed from frontend/public/logo.svg (viewBox 0 0
# 64 64): a rounded speech bubble with two message lines, a tail, and an accent
# dot. Redrawn with Pillow rather than loading the SVG so the tray needs no
# rasteriser dependency and no asset lookup that a source checkout might not
# have built yet. Keep in sync if the mark itself changes.
_BRAND = (14, 165, 233, 255)  # bubble, between the SVG gradient's two stops
_ACCENT = (251, 191, 36, 255)  # the amber dot
_IDLE = (145, 152, 161, 255)  # stopped: the same mark, drained of brand colour
_IDLE_ACCENT = (110, 117, 125, 255)

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
    """The Precursor mark, in brand colour when running and grey when stopped.

    Using the real logo rather than an abstract indicator makes the icon
    recognisable in a crowded menu bar; colour alone carries the state. The
    shape stays identical between the two so the icon doesn't appear to change
    identity when the instance stops.
    """
    image_mod = importlib.import_module("PIL.Image")
    draw_mod = importlib.import_module("PIL.ImageDraw")

    scale = _SUPERSAMPLE
    size = _ICON_SIZE * scale
    image = image_mod.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = draw_mod.Draw(image)

    body = _BRAND if running else _IDLE
    accent = _ACCENT if running else _IDLE_ACCENT

    def s(*values: float) -> list[float]:
        return [v * scale for v in values]

    # Bubble body: rect(6, 8, 52x40, r=12) in SVG units.
    draw.rounded_rectangle(s(6, 8, 58, 48), radius=12 * scale, fill=body)
    # Tail: the SVG's polygon(24 48, 24 58, 34 48).
    draw.polygon([(*s(24, 48),), (*s(24, 58),), (*s(34, 48),)], fill=body)
    # Two message lines, the upper one lighter (SVG opacity 0.75).
    draw.line(s(22, 30, 40, 30), fill=(255, 255, 255, 255), width=4 * scale)
    draw.line(s(22, 22, 34, 22), fill=(255, 255, 255, 191), width=4 * scale)
    # Accent dot, punched out of the bubble like the original.
    draw.ellipse(s(40, 12, 52, 24), fill=accent)

    return image.resize((_ICON_SIZE, _ICON_SIZE), image_mod.LANCZOS)


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

    def _stale_build(self) -> str | None:
        """The instance's version, when this process is running a different one.

        Updating replaces the code on disk, but the tray goes on executing what
        it imported at startup — including its own ``__version__``. It therefore
        compares the release it was *started* with against the published build,
        forever, and keeps offering an update that is already installed.

        The instance is the antidote: it is a fresh process, and ``runtime.json``
        records the version it actually launched with. A disagreement means this
        icon, not the app, is the stale one — detected within a poll interval,
        with no extra network call and no new state.
        """
        state = self._status.state
        if not self._status.running or state is None:
            # A stopped instance is no evidence either way.
            return None
        version = state.version.strip()
        if not version or version == _OWN_VERSION:
            return None
        return version

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

    def _reveal_data_dir(self, *_: object) -> None:
        # Synchronous: handing a path to the file manager returns immediately,
        # and the busy-state dance would only make the menu flicker.
        path = Path(get_settings().data_dir).resolve()
        try:
            desktop.reveal(path)
        except desktop.RevealError as exc:
            logger.error("Could not open the data folder: %s", exc)
            self._notify("Could not open the data folder", str(exc))

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
            self._restart_self()

        self._in_background("updating", _run)

    def _restart_self(self) -> bool:
        """Bounce the icon so it stops running the release it was started with.

        Updating replaces the code on disk, but this process keeps executing
        what it imported at startup — including its own menu actions. Since the
        tray is what drives the supervisor, leaving it stale means the icon
        offers the previous version's behaviour indefinitely.

        Restarting is fatal to *this* process by design: the service manager
        brings a fresh icon straight back. Returns whether the restart was
        asked for, so a hand-started icon can be told rather than left waiting
        for something that will never happen.
        """
        from precursor.backend import autostart

        if not autostart.info(autostart.TRAY).controllable:
            # Started by hand, so its lifecycle isn't ours to manage.
            return False
        # Give the notification a moment to reach the notification centre
        # before this process stops existing.
        time.sleep(1.5)
        try:
            autostart.restart_unit(autostart.TRAY)
        except autostart.AutostartError as exc:  # pragma: no cover - platform dependent
            logger.warning("Could not restart the tray after updating: %s", exc)
            return False
        return True

    def _restart_icon(self, *_: object) -> None:
        """Recover from having noticed the instance moved on without us.

        The cached update result was measured against this process's own stale
        version, so it is dropped rather than kept on show: whatever happens to
        the icon next, it stops making a claim it can no longer back up.
        """

        def _run() -> None:
            updates.invalidate()
            self._update = None
            if not self._restart_self():
                self._notify(
                    "Restart the icon",
                    "No login item manages this tray — quit it and run "
                    "`precursor tray` again to pick up the installed build.",
                )

        self._in_background("restarting the icon", _run)

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
        if self._stale_build() is not None:
            # Offering an update for a build already installed would be the one
            # thing this entry must not do.
            return "Restart the icon (running an older build)"
        info = self._update
        if info is None:
            return "Check for updates"
        if info.error:
            return "Check for updates (last check failed)"
        if info.update_available:
            return f"Update to {info.latest_version or 'latest'} and restart"
        return "Check for updates"

    def _update_action(self, *_: object) -> None:
        if self._stale_build() is not None:
            self._restart_icon()
        elif self._update is not None and self._update.update_available:
            self._apply_update()
        else:
            self._check_now()

    @staticmethod
    def _reveal_label() -> str:
        """Name the file manager the platform actually uses."""
        if sys.platform == "darwin":
            return "Reveal data folder in Finder"
        if os.name == "nt":
            return "Show data folder in Explorer"
        return "Open data folder"

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
            # Deliberately not gated on the instance running: the database and
            # the logs are exactly what you want to reach when it *won't* start.
            pystray.MenuItem(self._reveal_label(), self._reveal_data_dir),
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
                self._update_action,
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
