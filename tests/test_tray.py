"""The tray icon and menu.

The GUI bindings live behind the ``tray`` extra, so most of this file skips when
they aren't installed. What must hold regardless: the module stays importable
without them (the CLI dispatches to it before it can know), and it degrades with
an instruction rather than a traceback.
"""

from __future__ import annotations

import os
import sys

import pytest

from precursor.backend import tray

requires_gui = pytest.mark.skipif(
    not tray.gui_available(), reason="needs the `tray` extra (pystray + Pillow)"
)

_BRAND_RGB = tray._BRAND[:3]
_ACCENT_RGB = tray._ACCENT[:3]


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
    for running in (True, False):
        image = tray._make_image(running)
        assert image.size == (tray._ICON_SIZE, tray._ICON_SIZE)
        assert image.mode == "RGBA"


@requires_gui
def test_state_is_carried_by_colour_not_shape() -> None:
    """Running and stopped must be the same mark, so the icon doesn't appear to
    change identity — but distinguishable at a glance."""
    running = tray._make_image(True)
    stopped = tray._make_image(False)
    assert running.tobytes() != stopped.tobytes()

    # Same silhouette: identical alpha channel, differing colour.
    assert running.getchannel("A").tobytes() == stopped.getchannel("A").tobytes()


@requires_gui
def test_the_mark_is_drawn_where_the_logo_puts_it() -> None:
    """Spot-check the transcription against frontend/public/logo.svg.

    The SVG's viewBox is 0 0 64 64 and the icon is 64px, so its coordinates map
    across 1:1 — a drifting shape shows up here as a wrong colour at a point.
    """
    image = tray._make_image(True)

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
    image = tray._make_image(False)
    for x, y in ((14, 40), (46, 18), (26, 52)):
        r, g, b, _ = image.getpixel((x, y))  # type: ignore[misc]
        # Grey: the channels stay close together.
        assert max(r, g, b) - min(r, g, b) < 40, ((x, y), (r, g, b))


@requires_gui
def test_menu_offers_the_data_folder_whether_or_not_it_is_running() -> None:
    """The database and logs are exactly what you need when it *won't* start, so
    this entry must not be gated on a running instance."""
    app = tray.TrayApp(check_updates=False)
    labels = [str(item.text) for item in app._build_menu()]
    assert tray.TrayApp._reveal_label() in labels

    item = next(i for i in app._build_menu() if str(i.text) == tray.TrayApp._reveal_label())
    assert item.enabled is True


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

    tray.TrayApp(check_updates=False)._restart_self()
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
    tray.TrayApp(check_updates=False)._restart_self()


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
