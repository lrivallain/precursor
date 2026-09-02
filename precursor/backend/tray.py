"""``precursor tray`` — a menu-bar / system-tray control for the instance.

The tray owns no state of its own. It polls the supervisor (which reads
``runtime.json``) for "running or not", and the update service for "is there
something newer", then exposes the things worth a click: open, logs, start/stop,
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
from typing import Any, Literal

# The release *this process* is executing. `precursor.__version__` is resolved
# once, at import, so a long-lived tray keeps measuring against the build it was
# started with — which is exactly what makes it the thing to compare the running
# instance against. Aliased so the staleness check reads as a deliberate
# snapshot rather than a live lookup.
from precursor import __version__ as _OWN_VERSION
from precursor.backend import desktop, notifications, supervisor
from precursor.backend.config import get_settings
from precursor.backend.logging_config import TRAY_LOG_FILENAME, configure_logging, log_path
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

# What the icon can say about the instance. "busy" exists because starting,
# stopping and — above all — *updating* used to render exactly like "running":
# the icon claimed the app was up and clickable while it was being replaced.
IconState = Literal["running", "stopped", "busy"]

# The coloured bullets the menu leads its status line with. Text is the only
# thing a tray menu item carries on every platform, so state has to be spelled
# rather than drawn — and a bullet reads at a glance where a sentence doesn't.
_MARK_OK = "🟢"
_MARK_UPDATE = "🟡"
_MARK_ERROR = "🔴"
_MARK_UNKNOWN = "⚪"

# The buttons an actionable "update available" notification offers.
_APPLY = notifications.Choice("apply", "Update and restart")
_LATER = notifications.Choice("later", "Later")

# The busy label the status line reacts to, named once so the two places that
# have to agree — the action that sets it and the menu that reads it — can't
# drift apart.
_UPDATING = "updating"

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


def _make_image(state: IconState) -> Any:
    """The Precursor mark, coloured by what the instance is currently doing.

    Using the real logo rather than an abstract indicator makes the icon
    recognisable in a crowded menu bar. *Running* and *stopped* are the same
    silhouette in different colours, so the icon never appears to change
    identity; *busy* is the only one that changes the glyph — the bubble's two
    message lines become an ellipsis, the universal "working on it" — because
    "grey" alone would read as "stopped" while an update is in flight.
    """
    image_mod = importlib.import_module("PIL.Image")
    draw_mod = importlib.import_module("PIL.ImageDraw")

    scale = _SUPERSAMPLE
    size = _ICON_SIZE * scale
    image = image_mod.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = draw_mod.Draw(image)

    running = state == "running"
    body = _BRAND if running else _IDLE
    accent = _ACCENT if running else _IDLE_ACCENT

    def s(*values: float) -> list[float]:
        return [v * scale for v in values]

    # Bubble body: rect(6, 8, 52x40, r=12) in SVG units.
    draw.rounded_rectangle(s(6, 8, 58, 48), radius=12 * scale, fill=body)
    # Tail: the SVG's polygon(24 48, 24 58, 34 48).
    draw.polygon([(*s(24, 48),), (*s(24, 58),), (*s(34, 48),)], fill=body)
    if state == "busy":
        # Three dots, clear of the accent dot at (46, 18) r 6.
        for cx in (20, 30, 40):
            draw.ellipse(s(cx - 4, 23, cx + 4, 31), fill=(255, 255, 255, 255))
    else:
        # Two message lines, the upper one lighter (SVG opacity 0.75).
        draw.line(s(22, 30, 40, 30), fill=(255, 255, 255, 255), width=4 * scale)
        draw.line(s(22, 22, 34, 22), fill=(255, 255, 255, 191), width=4 * scale)
    # Accent dot, punched out of the bubble like the original.
    draw.ellipse(s(40, 12, 52, 24), fill=accent)

    return image.resize((_ICON_SIZE, _ICON_SIZE), image_mod.LANCZOS)


def _notify_mode() -> str:
    """What the tray does when a background check finds a new build.

    A dialog you didn't ask for is intrusive by nature, so it has to be
    refusable without giving up update checks altogether.
    """
    mode = get_settings().update_notify.strip().lower()
    return mode if mode in ("prompt", "notify", "off") else "prompt"


class TrayApp:
    def __init__(self, *, check_updates: bool = True) -> None:
        self._check_updates = check_updates
        self._stop = threading.Event()
        self._status = supervisor.status()
        self._update: updates.UpdateInfo | None = None
        self._busy: str | None = None
        self._icon: Any | None = None
        # The build the user has already been told about, so a check every half
        # hour doesn't turn into a prompt every half hour.
        self._announced: str | None = None
        self._prompting = False

    # --- state -----------------------------------------------------------

    @property
    def _running(self) -> bool:
        return self._status.running

    def _icon_state(self) -> IconState:
        # Busy wins: mid-update the app may still answer on its old port, and an
        # icon that looks "ready" while its code is being replaced is a lie.
        if self._busy:
            return "busy"
        return "running" if self._running else "stopped"

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
            self._icon.icon = _make_image(self._icon_state())
            self._icon.title = self._title()
            self._icon.update_menu()

    def _title(self) -> str:
        if self._busy:
            return f"Precursor — {self._busy}"
        if self._status.running and self._status.state is not None:
            return f"Precursor — running on :{self._status.state.port}"
        return "Precursor — stopped"

    def _log_file(self) -> Path:
        state = self._status.state
        # Prefer what the supervisor recorded: an instance started with a
        # different data directory logs somewhere this process's own settings
        # would never point at.
        if state is not None and state.log_file:
            return Path(state.log_file)
        return log_path()

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

    def _open_log(self, *_: object) -> None:
        """Open the instance log — the first thing to look at when it misbehaves.

        Falls back to the containing folder when there is no log yet, because
        "nothing happened" is the least useful answer available: a fresh install
        that has never started still has launchd's own stderr capture in there.
        """
        log_file = self._log_file()
        try:
            desktop.open_file(log_file)
        except desktop.RevealError:
            try:
                desktop.reveal(log_file.parent)
            except desktop.RevealError as exc:
                logger.error("Could not open the log: %s", exc)
                self._notify("Could not open the log", str(exc))

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

        self._in_background(_UPDATING, _run)

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
            elif info.update_available and self._stale_build() is None:
                # Asked for explicitly, so re-offer it even if the background
                # check already announced this build once.
                self._announce_update(info, force=True)
            elif info.update_available:
                self._notify(
                    "Already installed",
                    f"{info.latest_version or 'The published build'} is on disk — "
                    "restart the icon.",
                )
            else:
                self._notify("Up to date", info.current_version)

        self._in_background("checking for updates", _run)

    # --- announcing a new build ------------------------------------------

    def _announce_update(self, info: updates.UpdateInfo, *, force: bool = False) -> None:
        """Say a new build exists — and, where the desktop allows, offer to take it.

        Noticing an update in the background is only half the feature: without a
        way to act on it, the user still has to find the menu. So this raises an
        *actionable* notification where the platform has one, and a plain toast
        where it doesn't.

        Announced at most once per published build (``force`` overrides, for a
        check the user asked for), because a half-hourly poll must not become a
        half-hourly interruption. A prompt already on screen is never doubled,
        forced or not — that one is the user's turn to answer.
        """
        mode = _notify_mode()
        if self._prompting:
            return
        if mode == "off" and not force:
            return
        key = info.latest_commit or info.latest_version or ""
        if not force and key == self._announced:
            return
        self._announced = key
        headline = info.latest_version or "A newer build"
        detail = f"{info.current_version} → {headline}"

        if mode == "notify" or not notifications.can_ask():
            self._notify("Precursor update available", detail)
            return

        def _prompt() -> None:
            try:
                choice = notifications.ask(
                    "Precursor update available",
                    f"{detail}\n\nInstall it now and restart?",
                    (_APPLY, _LATER),
                    default=_APPLY.key,
                )
            finally:
                self._prompting = False
            # Anything other than an explicit yes — dismissed, timed out,
            # "Later" — leaves the build waiting in the menu.
            if choice == _APPLY.key:
                self._apply_update()

        # Set before the thread starts, not inside it: the flag exists to stop a
        # second announcement stacking a second dialog, and a thread that hasn't
        # been scheduled yet would leave that window open.
        self._prompting = True
        # Off the caller's thread: the prompt blocks until answered, and the
        # poll loop (or the busy state) must not be held hostage by a dialog
        # nobody is looking at.
        threading.Thread(target=_prompt, daemon=True).start()

    def _quit(self, *_: object) -> None:
        self._stop.set()
        if self._icon is not None:
            self._icon.stop()

    # --- menu ------------------------------------------------------------

    def _update_status(self) -> str:
        """One line saying where this install stands, led by a coloured bullet.

        The action entry below can only describe *the next click*; this says
        what is actually true — which is the question you open the menu to
        answer ("did it update?", "is it still checking?").
        """
        stale = self._stale_build()
        if stale is not None:
            return f"{_MARK_UPDATE} {stale} is installed — this icon is older"
        if self._busy == _UPDATING:
            # The cached result is being invalidated as we speak; repeating "up
            # to date" during the one operation that changes it would be the
            # least trustworthy moment to say it.
            waiting = self._update.latest_version if self._update else None
            return f"{_MARK_UNKNOWN} Installing {waiting or 'the update'}…"
        info = self._update
        if info is None:
            # Automatic checks off, but a manual one still fills this in — so
            # "off" is what we say only while there is genuinely nothing to say.
            pending = "Checking for updates…" if self._check_updates else "Update checks are off"
            return f"{_MARK_UNKNOWN} {pending} — {_OWN_VERSION}"
        if info.error:
            return f"{_MARK_ERROR} Could not check for updates — {info.current_version}"
        if info.update_available:
            return f"{_MARK_UPDATE} Update available — {info.latest_version or 'newer build'}"
        return f"{_MARK_OK} Up to date — {info.current_version}"

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

    _LOG_LABEL = "Open log file"

    def _build_menu(self) -> Any:
        pystray = _pystray()

        return pystray.Menu(
            pystray.MenuItem(lambda _: self._title(), lambda *_: None, enabled=False),
            pystray.MenuItem(lambda _: self._update_status(), lambda *_: None, enabled=False),
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
            pystray.MenuItem(self._LOG_LABEL, self._open_log),
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
                    self._refresh_update_info()
                except Exception as exc:  # pragma: no cover - network dependent
                    logger.debug("Background update check failed: %s", exc)

    def run(self) -> int:
        pystray = _pystray()

        self._icon = pystray.Icon(
            "precursor",
            icon=_make_image(self._icon_state()),
            title=self._title(),
            menu=self._build_menu(),
        )
        threading.Thread(target=self._poll_loop, daemon=True).start()
        if self._check_updates:
            self._in_background("checking for updates", self._refresh_update_info)
        self._icon.run()
        return 0

    def _refresh_update_info(self) -> None:
        info = updates.check()
        self._update = info
        # A check nobody asked for is the one worth speaking up about: the menu
        # would otherwise sit there knowing about a new build until the next
        # time somebody happened to click the icon.
        if info.update_available and not info.error and self._stale_build() is None:
            self._announce_update(info)


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

    # The tray is a long-lived process of its own, and until now it configured
    # no logging at all: every `logger.error` in this module reached only
    # `logging.lastResort`, i.e. an unformatted line on a stderr that a login
    # item throws away. Its own file, not the app's — two processes rotating one
    # file race, and "the icon failed" is a different question from "the server
    # failed" anyway.
    configure_logging(get_settings().log_level, log_file=log_path(TRAY_LOG_FILENAME))

    if not gui_available():
        print(_missing_deps_message(), file=sys.stderr)
        return 1

    if args.start and not supervisor.status().running:
        try:
            supervisor.start()
        except supervisor.SupervisorError as exc:
            print(f"error: {exc}", file=sys.stderr)

    return TrayApp(check_updates=not args.no_update_check).run()
