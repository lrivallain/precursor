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

from precursor.backend import supervisor, tray
from precursor.backend.services import updates

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
