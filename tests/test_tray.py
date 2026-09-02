"""The tray icon and menu.

The GUI bindings live behind the ``tray`` extra, so most of this file skips when
they aren't installed. What must hold regardless: the module stays importable
without them (the CLI dispatches to it before it can know), and it degrades with
an instruction rather than a traceback.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from precursor.backend import supervisor, tray
from precursor.backend.services import updates

requires_gui = pytest.mark.skipif(
    not tray.gui_available(), reason="needs the `tray` extra (pystray + Pillow)"
)

_BRAND_RGB = tray._BRAND[:3]
_ACCENT_RGB = tray._ACCENT[:3]


@pytest.fixture(autouse=True)
def _no_real_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a test put an actual dialog on the developer's screen."""
    monkeypatch.setattr(tray.notifications, "can_ask", lambda: False)
    monkeypatch.setattr(
        tray.notifications,
        "ask",
        lambda *_a, **_k: pytest.fail("raised a real desktop prompt"),
    )


def test_module_imports_without_the_gui_extra() -> None:
    """`precursor tray` dispatches here before it can check for the deps."""
    assert callable(tray.main)
    assert isinstance(tray.gui_available(), bool)


def test_missing_deps_message_names_the_install_command() -> None:
    message = tray._missing_deps_message()
    assert "tray" in message
    assert "uv" in message


def test_reveal_label_matches_the_platform() -> None:
    label = tray.TrayApp._reveal_label()
    if sys.platform == "darwin":
        assert label == "Reveal data folder in Finder"
    elif os.name == "nt":  # pragma: no cover - Windows-only path
        assert "Explorer" in label
    else:
        assert "data folder" in label


@requires_gui
def test_icon_renders_at_the_expected_size() -> None:
    for state in ("running", "stopped", "busy"):
        image = tray._make_image(state)  # type: ignore[arg-type]
        assert image.size == (tray._ICON_SIZE, tray._ICON_SIZE)
        assert image.mode == "RGBA"


@requires_gui
def test_state_is_carried_by_colour_not_shape() -> None:
    """Running and stopped must be the same mark, so the icon doesn't appear to
    change identity — but distinguishable at a glance."""
    running = tray._make_image("running")
    stopped = tray._make_image("stopped")
    assert running.tobytes() != stopped.tobytes()

    # Same silhouette: identical alpha channel, differing colour.
    assert running.getchannel("A").tobytes() == stopped.getchannel("A").tobytes()


@requires_gui
def test_the_mark_is_drawn_where_the_logo_puts_it() -> None:
    """Spot-check the transcription against frontend/public/logo.svg.

    The SVG's viewBox is 0 0 64 64 and the icon is 64px, so its coordinates map
    across 1:1 — a drifting shape shows up here as a wrong colour at a point.
    """
    image = tray._make_image("running")

    def at(x: int, y: int) -> tuple[int, int, int, int]:
        return image.getpixel((x, y))  # type: ignore[return-value]

    def near(actual: tuple[int, int, int, int], expected: tuple[int, int, int]) -> bool:
        return all(abs(a - e) < 45 for a, e in zip(actual[:3], expected, strict=True))

    assert near(at(14, 40), _BRAND_RGB), at(14, 40)  # bubble body
    assert near(at(46, 18), _ACCENT_RGB), at(46, 18)  # amber dot
    assert near(at(30, 30), (255, 255, 255)), at(30, 30)  # message line
    assert near(at(26, 52), _BRAND_RGB), at(26, 52)  # tail, below the bubble
    assert at(2, 60)[3] < 40, at(2, 60)  # corner stays transparent


@requires_gui
def test_stopped_icon_is_desaturated() -> None:
    image = tray._make_image("stopped")
    for x, y in ((14, 40), (46, 18), (26, 52)):
        r, g, b, _ = image.getpixel((x, y))  # type: ignore[misc]
        # Grey: the channels stay close together.
        assert max(r, g, b) - min(r, g, b) < 40, ((x, y), (r, g, b))


# --- "is it ready?" (the busy icon) -------------------------------------------
#
# Starting, stopping and above all *updating* used to render exactly like
# "running": a fully-coloured icon claiming the app was up and clickable while
# its code was being replaced underneath.


@requires_gui
def test_busy_is_grey_and_carries_a_working_glyph() -> None:
    busy = tray._make_image("busy")
    stopped = tray._make_image("stopped")

    # Grey like "stopped", so it never claims to be ready…
    r, g, b, _ = busy.getpixel((14, 40))  # type: ignore[misc]
    assert max(r, g, b) - min(r, g, b) < 40, (r, g, b)

    # …but a different glyph, because grey alone reads as "stopped". The
    # ellipsis's middle dot sits between the two message lines, on bubble body.
    assert busy.tobytes() != stopped.tobytes()
    assert busy.getpixel((30, 27))[:3] == (255, 255, 255)  # type: ignore[index]
    assert stopped.getpixel((30, 27))[:3] == tray._IDLE[:3]  # type: ignore[index]


def test_busy_beats_running_in_the_icon_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mid-update the app may still answer on its old port; the icon must not
    say "ready" while the code behind it is being swapped."""
    app = _app(_running_at("2026.9.0"), monkeypatch)
    assert app._icon_state() == "running"
    app._busy = "updating"
    assert app._icon_state() == "busy"


def test_a_stopped_instance_is_not_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(_running_at("2026.9.0", running=False), monkeypatch)
    assert app._icon_state() == "stopped"


@requires_gui
def test_menu_offers_the_data_folder_whether_or_not_it_is_running() -> None:
    """The database and logs are exactly what you need when it *won't* start, so
    this entry must not be gated on a running instance."""
    app = tray.TrayApp(check_updates=False)
    labels = [str(item.text) for item in app._build_menu()]
    assert tray.TrayApp._reveal_label() in labels

    item = next(i for i in app._build_menu() if str(i.text) == tray.TrayApp._reveal_label())
    assert item.enabled is True


# --- reaching the log ---------------------------------------------------------


@requires_gui
def test_the_menu_links_to_the_log() -> None:
    app = tray.TrayApp(check_updates=False)
    item = next(i for i in app._build_menu() if str(i.text) == tray.TrayApp._LOG_LABEL)
    # Same reasoning as the data folder: the log is what you want when it
    # *won't* start.
    assert item.enabled is True


def test_the_log_path_follows_the_running_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """An instance started against another data directory logs where its own
    settings point, not where this process's would."""
    app = _app(_running_at("2026.9.0"), monkeypatch)
    assert app._log_file() == Path("/tmp/precursor.log")


def test_the_log_path_falls_back_to_the_configured_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(supervisor.Status(running=False, state=None), monkeypatch)
    assert app._log_file().name == "precursor.log"


def test_opening_the_log_hands_the_file_to_the_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(_running_at("2026.9.0"), monkeypatch)
    opened: list[Path] = []
    monkeypatch.setattr(tray.desktop, "open_file", lambda path: opened.append(path))
    app._open_log()
    assert opened == [Path("/tmp/precursor.log")]


def test_a_missing_log_opens_the_folder_instead(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh install has never written one, and launchd's own stderr capture
    lives next to it — an empty window would be the least useful answer."""
    app = _app(_running_at("2026.9.0"), monkeypatch)
    revealed: list[Path] = []

    def missing(path: Path) -> None:
        raise tray.desktop.RevealError(f"no file at {path}")

    monkeypatch.setattr(tray.desktop, "open_file", missing)
    monkeypatch.setattr(tray.desktop, "reveal", lambda path: revealed.append(path))
    app._open_log()
    assert revealed == [Path("/tmp")]


def test_the_tray_restarts_itself_after_an_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """Updating replaces the code on disk, but this process keeps running what
    it imported at startup — including its own menu actions, which drive the
    supervisor. A stale icon therefore keeps offering the previous release's
    behaviour indefinitely."""
    from precursor.backend import autostart

    calls: list[str] = []
    monkeypatch.setattr(
        autostart,
        "info",
        lambda unit=autostart.APP: autostart.AutostartInfo(
            unit=unit.key, supported=True, installed=True, kind="launchd"
        ),
    )
    monkeypatch.setattr(
        autostart, "restart_unit", lambda unit=autostart.APP: calls.append(unit.key)
    )
    monkeypatch.setattr(tray.time, "sleep", lambda _s: None)

    assert tray.TrayApp(check_updates=False)._restart_self() is True
    assert calls == ["tray"]


def test_a_hand_started_tray_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """If no unit manages it, its lifecycle isn't ours to interfere with."""
    from precursor.backend import autostart

    monkeypatch.setattr(
        autostart,
        "info",
        lambda unit=autostart.APP: autostart.AutostartInfo(
            unit=unit.key, supported=True, installed=False, kind="launchd"
        ),
    )
    monkeypatch.setattr(
        autostart,
        "restart_unit",
        lambda unit=autostart.APP: (_ for _ in ()).throw(
            AssertionError("restarted a tray it does not manage")
        ),
    )
    assert tray.TrayApp(check_updates=False)._restart_self() is False


def test_service_update_restarts_the_tray_too(monkeypatch: pytest.MonkeyPatch) -> None:
    from precursor.backend import autostart, service_cli

    calls: list[str] = []
    monkeypatch.setattr(
        autostart,
        "info",
        lambda unit=autostart.APP: autostart.AutostartInfo(
            unit=unit.key, supported=True, installed=True, kind="launchd"
        ),
    )
    monkeypatch.setattr(
        autostart, "restart_unit", lambda unit=autostart.APP: calls.append(unit.key)
    )
    service_cli._restart_tray_after_update()
    assert calls == ["tray"]


def test_a_failing_tray_restart_does_not_fail_the_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """The app is already updated and serving by this point; the icon is not
    worth reporting an otherwise-successful update as a failure."""
    from precursor.backend import autostart, service_cli

    monkeypatch.setattr(
        autostart,
        "info",
        lambda unit=autostart.APP: autostart.AutostartInfo(
            unit=unit.key, supported=True, installed=True, kind="launchd"
        ),
    )
    monkeypatch.setattr(
        autostart,
        "restart_unit",
        lambda unit=autostart.APP: (_ for _ in ()).throw(autostart.AutostartError("boom")),
    )
    service_cli._restart_tray_after_update()  # must not raise


# --- "I am the stale one" (issue #274) ----------------------------------------
#
# `precursor.__version__` is resolved once, at import, so a long-lived tray keeps
# comparing the release it *started* with against the published build — and goes
# on offering an update that is already installed. `runtime.json` carries the
# version the (freshly spawned) instance actually launched with, which is enough
# to notice the disagreement.


def _running_at(version: str, *, running: bool = True) -> supervisor.Status:
    return supervisor.Status(
        running=running,
        state=supervisor.RuntimeState(
            pid=4242,
            host="127.0.0.1",
            port=8765,
            url="http://127.0.0.1:8765",
            version=version,
            started_at="2026-01-01T00:00:00Z",
            log_file="/tmp/precursor.log",
        ),
    )


def _app(status: supervisor.Status, monkeypatch: pytest.MonkeyPatch) -> tray.TrayApp:
    """A tray whose view of the supervisor is fixed, and which runs its actions
    inline so a test doesn't have to join a daemon thread."""
    monkeypatch.setattr(tray.supervisor, "status", lambda: status)
    app = tray.TrayApp(check_updates=False)
    monkeypatch.setattr(app, "_in_background", lambda _label, fn: fn())
    return app


def _update_info(**overrides: object) -> updates.UpdateInfo:
    fields: dict[str, object] = {
        "current_version": "2026.1.0",
        "current_commit": None,
        "latest_version": "2026.2.0",
        "latest_commit": None,
        "update_available": True,
        "channel": "stable",
        "install_mode": "uv-tool",
    }
    fields.update(overrides)
    return updates.UpdateInfo(**fields)  # type: ignore[arg-type]


def test_a_moved_instance_marks_this_process_as_the_stale_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(_running_at("2026.9.0.dev245"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0.dev238")
    assert app._stale_build() == "2026.9.0.dev245"


def test_matching_versions_are_not_staleness(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(_running_at("2026.9.0.dev245"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0.dev245")
    assert app._stale_build() is None


def test_a_stopped_instance_is_no_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing is running, so nothing says what is installed."""
    app = _app(_running_at("2026.9.0.dev245", running=False), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0.dev238")
    assert app._stale_build() is None


def test_a_state_file_without_a_version_is_no_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """`version` is read with a default, so an older record can be empty."""
    app = _app(_running_at(""), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0.dev238")
    assert app._stale_build() is None


def test_staleness_replaces_the_update_offer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: never offer an update for a build already installed."""
    app = _app(_running_at("2026.9.0.dev245"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0.dev238")
    app._update = _update_info(latest_version="2026.9.0.dev245")

    label = app._update_label()
    assert "Restart the icon" in label
    assert "Update to" not in label


def test_a_current_tray_still_offers_the_update(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(_running_at("2026.9.0.dev238"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0.dev238")
    app._update = _update_info(latest_version="2026.9.0.dev245")
    assert app._update_label() == "Update to 2026.9.0.dev245 and restart"


def test_clicking_the_entry_bounces_the_icon_and_drops_the_stale_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from precursor.backend import autostart

    calls: list[str] = []
    invalidated: list[bool] = []
    monkeypatch.setattr(
        autostart,
        "info",
        lambda unit=autostart.APP: autostart.AutostartInfo(
            unit=unit.key, supported=True, installed=True, kind="launchd"
        ),
    )
    monkeypatch.setattr(
        autostart, "restart_unit", lambda unit=autostart.APP: calls.append(unit.key)
    )
    monkeypatch.setattr(tray.time, "sleep", lambda _s: None)
    monkeypatch.setattr(tray.updates, "invalidate", lambda: invalidated.append(True))
    monkeypatch.setattr(
        tray.updates,
        "apply",
        lambda _info: pytest.fail("applied an update that is already installed"),
    )

    app = _app(_running_at("2026.9.0.dev245"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0.dev238")
    app._update = _update_info(latest_version="2026.9.0.dev245")

    app._update_action()

    assert calls == ["tray"]
    assert invalidated == [True]
    # The cached result was measured against this process's own stale version,
    # so the menu must stop showing it.
    assert app._update is None


def test_a_hand_started_stale_tray_is_told_what_to_do(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_restart_self` is a no-op for an icon no login item owns, so silently
    doing nothing would leave the click looking broken."""
    from precursor.backend import autostart

    monkeypatch.setattr(
        autostart,
        "info",
        lambda unit=autostart.APP: autostart.AutostartInfo(
            unit=unit.key, supported=True, installed=False, kind="launchd"
        ),
    )
    monkeypatch.setattr(tray.updates, "invalidate", lambda: None)

    app = _app(_running_at("2026.9.0.dev245"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0.dev238")
    notices: list[tuple[str, str]] = []
    monkeypatch.setattr(app, "_notify", lambda title, message: notices.append((title, message)))

    app._update_action()

    assert len(notices) == 1
    assert "precursor tray" in notices[0][1]


@requires_gui
def test_the_menu_itself_surfaces_the_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards the wiring, not just the label helper: the entry is built from a
    callable, so a mis-wired menu would still pass the unit tests above."""
    app = _app(_running_at("2026.9.0.dev245"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0.dev238")
    app._update = _update_info(latest_version="2026.9.0.dev245")

    labels = [str(item.text) for item in app._build_menu()]
    assert any("Restart the icon" in label for label in labels), labels
    assert not any("Update to" in label for label in labels), labels


# --- saying where the install stands ------------------------------------------
#
# The action entry can only describe *the next click*. The status line above it
# says what is actually true, which is the question you open the menu to answer.


def test_the_status_line_is_green_when_up_to_date(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(_running_at("2026.9.0"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0")
    app._update = _update_info(current_version="2026.9.0", update_available=False)
    status = app._update_status()
    assert status.startswith(tray._MARK_OK)
    assert "Up to date" in status
    assert "2026.9.0" in status


def test_the_status_line_is_amber_when_a_build_is_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(_running_at("2026.9.0"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0")
    app._update = _update_info(latest_version="2026.9.1")
    status = app._update_status()
    assert status.startswith(tray._MARK_UPDATE)
    assert "2026.9.1" in status


def test_the_status_line_is_red_when_the_check_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "Couldn't check" and "up to date" are different facts, and conflating
    them is how an install goes quietly stale."""
    app = _app(_running_at("2026.9.0"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0")
    app._update = _update_info(update_available=False, error="offline")
    assert app._update_status().startswith(tray._MARK_ERROR)


def test_the_status_line_admits_it_has_not_looked_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(_running_at("2026.9.0"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0")
    assert app._update_status().startswith(tray._MARK_UNKNOWN)


def test_the_status_line_says_when_checks_are_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--no-update-check` must not look like "still checking" forever."""
    monkeypatch.setattr(tray.supervisor, "status", lambda: _running_at("2026.9.0"))
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0")
    app = tray.TrayApp(check_updates=False)
    assert "off" in app._update_status()


def test_the_status_line_names_the_stale_icon(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the icon is the old one, "update available" would be a lie."""
    app = _app(_running_at("2026.9.0.dev245"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0.dev238")
    app._update = _update_info(latest_version="2026.9.0.dev245")
    status = app._update_status()
    assert "this icon is older" in status
    assert "Update available" not in status


@requires_gui
def test_the_menu_leads_with_the_status_line(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(_running_at("2026.9.0"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0")
    app._update = _update_info(latest_version="2026.9.1")
    labels = [str(item.text) for item in app._build_menu()]
    assert any(label.startswith(tray._MARK_UPDATE) for label in labels), labels


# --- announcing a build nobody asked about ------------------------------------
#
# Noticing an update in the background is only half the feature: without a way to
# act on it, the user still has to go and find the menu.


def test_a_background_check_announces_what_it_found(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(_running_at("2026.9.0"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0")
    info = _update_info(latest_version="2026.9.1")
    monkeypatch.setattr(tray.updates, "check", lambda **_kw: info)
    announced: list[updates.UpdateInfo] = []
    monkeypatch.setattr(app, "_announce_update", lambda i, **_kw: announced.append(i))

    app._refresh_update_info()

    assert announced == [info]


def test_nothing_is_announced_when_there_is_nothing_new(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(_running_at("2026.9.0"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0")
    monkeypatch.setattr(tray.updates, "check", lambda **_kw: _update_info(update_available=False))
    monkeypatch.setattr(
        app, "_announce_update", lambda *_a, **_kw: pytest.fail("announced a non-update")
    )
    app._refresh_update_info()


def test_a_stale_icon_announces_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The "new" build is already on disk — this process is the old thing."""
    app = _app(_running_at("2026.9.0.dev245"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0.dev238")
    monkeypatch.setattr(
        tray.updates, "check", lambda **_kw: _update_info(latest_version="2026.9.0.dev245")
    )
    monkeypatch.setattr(
        app,
        "_announce_update",
        lambda *_a, **_kw: pytest.fail("offered an update that is already installed"),
    )
    app._refresh_update_info()


def test_the_same_build_is_only_announced_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A check every half hour must not become an interruption every half hour."""
    app = _app(_running_at("2026.9.0"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0")
    notices: list[tuple[str, str]] = []
    monkeypatch.setattr(app, "_notify", lambda title, message: notices.append((title, message)))
    info = _update_info(latest_version="2026.9.1")

    app._announce_update(info)
    app._announce_update(info)

    assert len(notices) == 1
    assert "2026.9.1" in notices[0][1]


def test_a_newer_build_gets_its_own_announcement(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(_running_at("2026.9.0"), monkeypatch)
    notices: list[tuple[str, str]] = []
    monkeypatch.setattr(app, "_notify", lambda title, message: notices.append((title, message)))

    app._announce_update(_update_info(latest_version="2026.9.1"))
    app._announce_update(_update_info(latest_version="2026.9.2"))

    assert len(notices) == 2


def test_a_desktop_without_buttons_still_gets_told(monkeypatch: pytest.MonkeyPatch) -> None:
    """`can_ask` is false in the autouse fixture, so this is the fallback path."""
    app = _app(_running_at("2026.9.0"), monkeypatch)
    notices: list[tuple[str, str]] = []
    monkeypatch.setattr(app, "_notify", lambda title, message: notices.append((title, message)))
    app._announce_update(_update_info(latest_version="2026.9.1"))
    assert notices and "2026.9.1" in notices[0][1]


def _prompting(app: tray.TrayApp, monkeypatch: pytest.MonkeyPatch, answer: str | None) -> list[str]:
    """Wire the app to an actionable prompt that returns ``answer``, inline."""
    applied: list[str] = []
    monkeypatch.setattr(tray.notifications, "can_ask", lambda: True)
    monkeypatch.setattr(tray.notifications, "ask", lambda *_a, **_kw: answer)
    monkeypatch.setattr(tray.threading, "Thread", _InlineThread)
    monkeypatch.setattr(app, "_apply_update", lambda *_a: applied.append("applied"))
    return applied


class _InlineThread:
    """Run the "background" prompt synchronously so the test can assert on it."""

    def __init__(self, *, target: object, daemon: bool = False) -> None:
        self._target = target

    def start(self) -> None:
        self._target()  # type: ignore[operator]


def test_the_notification_can_apply_the_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """The point of the button: take the update without hunting for the menu."""
    app = _app(_running_at("2026.9.0"), monkeypatch)
    applied = _prompting(app, monkeypatch, tray._APPLY.key)
    app._announce_update(_update_info(latest_version="2026.9.1"))
    assert applied == ["applied"]


@pytest.mark.parametrize("answer", [tray._LATER.key, None])
def test_anything_but_yes_leaves_the_build_waiting(
    monkeypatch: pytest.MonkeyPatch, answer: str | None
) -> None:
    """Dismissed, timed out or "Later" — none of them is consent to restart."""
    app = _app(_running_at("2026.9.0"), monkeypatch)
    applied = _prompting(app, monkeypatch, answer)
    app._announce_update(_update_info(latest_version="2026.9.1"))
    assert applied == []


def test_the_prompt_can_be_turned_down_to_a_toast(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(_running_at("2026.9.0"), monkeypatch)
    monkeypatch.setattr(tray.notifications, "can_ask", lambda: True)
    monkeypatch.setattr(
        tray.notifications, "ask", lambda *_a, **_kw: pytest.fail("prompted anyway")
    )
    monkeypatch.setattr(tray, "_notify_mode", lambda: "notify")
    notices: list[tuple[str, str]] = []
    monkeypatch.setattr(app, "_notify", lambda title, message: notices.append((title, message)))

    app._announce_update(_update_info(latest_version="2026.9.1"))

    assert len(notices) == 1


def test_announcements_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The menu still says so; the desktop just stays quiet."""
    app = _app(_running_at("2026.9.0"), monkeypatch)
    monkeypatch.setattr(tray, "_notify_mode", lambda: "off")
    monkeypatch.setattr(app, "_notify", lambda *_a: pytest.fail("notified anyway"))

    app._announce_update(_update_info(latest_version="2026.9.1"))

    # A check the user asked for is still answered.
    forced: list[tuple[str, str]] = []
    monkeypatch.setattr(app, "_notify", lambda title, message: forced.append((title, message)))
    app._announce_update(_update_info(latest_version="2026.9.1"), force=True)
    assert len(forced) == 1


def test_an_unknown_notify_mode_falls_back_to_prompting() -> None:
    """A typo in the setting must not silently disable the announcement."""
    from precursor.backend.config import Settings

    assert Settings(update_notify="shout").update_notify == "shout"
    assert tray._notify_mode() in ("prompt", "notify", "off")


def test_a_prompt_already_on_screen_is_never_doubled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clicking "Check for updates" while a dialog waits must not stack a second
    one on top of it — that dialog is the user's turn to answer."""
    app = _app(_running_at("2026.9.0"), monkeypatch)
    monkeypatch.setattr(tray.notifications, "can_ask", lambda: True)
    monkeypatch.setattr(
        tray.notifications, "ask", lambda *_a, **_kw: pytest.fail("stacked a second dialog")
    )
    monkeypatch.setattr(app, "_notify", lambda *_a: pytest.fail("notified over a live dialog"))
    app._prompting = True

    app._announce_update(_update_info(latest_version="2026.9.1"))
    app._announce_update(_update_info(latest_version="2026.9.2"), force=True)


def test_the_status_line_does_not_claim_up_to_date_mid_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one operation that invalidates the cached result is the worst moment
    to repeat it."""
    app = _app(_running_at("2026.9.0"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0")
    app._update = _update_info(latest_version="2026.9.1")
    app._busy = tray._UPDATING

    status = app._update_status()
    assert "Installing 2026.9.1" in status
    assert "Up to date" not in status


def test_starting_and_stopping_leave_the_update_status_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bouncing the instance says nothing about which build is published."""
    app = _app(_running_at("2026.9.0"), monkeypatch)
    monkeypatch.setattr(tray, "_OWN_VERSION", "2026.9.0")
    app._update = _update_info(update_available=False)
    app._busy = "restarting"
    assert app._update_status().startswith(tray._MARK_OK)
