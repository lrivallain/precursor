"""Update preferences: saved from Settings, read by the tray and the update check."""

from __future__ import annotations

from pathlib import Path

import pytest

from precursor.backend import config
from precursor.backend.services import update_prefs, updates


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRECURSOR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PRECURSOR_UPDATE_CHANNEL", raising=False)
    monkeypatch.delenv("PRECURSOR_UPDATE_NOTIFY", raising=False)
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()


def test_defaults_check_releases_quietly() -> None:
    prefs = update_prefs.load()
    assert prefs == update_prefs.UpdatePrefs()
    assert prefs.channel == "stable"
    assert prefs.checks_enabled is True
    assert prefs.notifications_enabled is False


def test_environment_supplies_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRECURSOR_UPDATE_CHANNEL", "Nightly")
    monkeypatch.setenv("PRECURSOR_UPDATE_NOTIFY", "off")
    config.get_settings.cache_clear()
    prefs = update_prefs.load()
    assert prefs.channel == "nightly"
    assert prefs.notifications_enabled is False


@pytest.mark.parametrize("legacy", ["prompt", "notify", "on"])
def test_any_mode_but_off_notifies(monkeypatch: pytest.MonkeyPatch, legacy: str) -> None:
    monkeypatch.setenv("PRECURSOR_UPDATE_NOTIFY", legacy)
    config.get_settings.cache_clear()
    assert update_prefs.load().notifications_enabled is True


def test_a_saved_choice_wins_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRECURSOR_UPDATE_CHANNEL", "nightly")
    config.get_settings.cache_clear()
    update_prefs.save(channel="stable", checks_enabled=False)
    prefs = update_prefs.load()
    assert prefs.channel == "stable"
    assert prefs.checks_enabled is False
    assert prefs.notifications_enabled is False


def test_save_merges_with_what_is_stored() -> None:
    update_prefs.save(notifications_enabled=False)
    update_prefs.save(channel="nightly")
    assert update_prefs.load() == update_prefs.UpdatePrefs(
        channel="nightly", notifications_enabled=False
    )


def test_a_corrupt_file_falls_back_to_defaults(_data_dir: Path) -> None:
    (_data_dir / "update-prefs.json").write_text("{not json", encoding="utf-8")
    assert update_prefs.load() == update_prefs.UpdatePrefs()
    (_data_dir / "update-prefs.json").write_text('{"channel": "beta", "checks_enabled": "no"}')
    assert update_prefs.load() == update_prefs.UpdatePrefs()


def test_the_update_check_follows_the_saved_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "__version__", "2026.7.0")
    assert updates.default_channel() == "stable"
    update_prefs.save(channel="nightly")
    assert updates.default_channel() == "nightly"


def test_settings_api_saves_update_prefs_to_the_file(_data_dir: Path) -> None:
    from fastapi.testclient import TestClient

    from precursor.backend.main import create_app

    with TestClient(create_app()) as client:
        before = client.get("/api/settings").json()
        assert before["update_checks_enabled"] is True
        assert before["update_notifications_enabled"] is False
        assert before["update_channel"] == "stable"

        saved = client.put(
            "/api/settings",
            json={
                "update_channel": "nightly",
                "update_checks_enabled": False,
                "update_notifications_enabled": False,
            },
        ).json()
        assert saved["update_channel"] == "nightly"
        assert saved["update_checks_enabled"] is False
        assert saved["update_notifications_enabled"] is False
        assert client.put("/api/settings", json={"update_channel": "beta"}).status_code == 422

    # Where the tray will look for them.
    assert update_prefs.load() == update_prefs.UpdatePrefs(
        channel="nightly", checks_enabled=False, notifications_enabled=False
    )
