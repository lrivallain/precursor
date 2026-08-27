"""State resolution for an installed (non-checkout) Precursor.

A source checkout keeps its database and data directory beside the code, which
is what makes every worktree an isolated sandbox. An installed wheel has no such
home — and is typically started by a login item whose working directory is
``/`` — so relying on the same relative defaults would silently create a fresh,
empty database wherever the launcher happened to start.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from precursor.backend import config


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_source_checkout_is_detected_in_the_repo() -> None:
    # The tests themselves run from a working copy, so this is self-verifying.
    assert config.is_source_checkout() is True


def test_installed_defaults_are_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "is_source_checkout", lambda: False)

    data_dir = Path(config._default_data_dir())
    assert data_dir.is_absolute()

    url = config._default_database_url()
    assert url.startswith("sqlite+aiosqlite:////") or url.startswith("sqlite+aiosqlite:///")
    # The whole point: not resolved against the process working directory.
    assert "///./" not in url
    assert str(config.user_data_dir().as_posix()) in url


def test_checkout_defaults_stay_repo_relative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "is_source_checkout", lambda: True)
    assert config._default_data_dir() == ".precursor"
    assert config._default_database_url() == "sqlite+aiosqlite:///./precursor.db"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS layout")
def test_macos_user_data_dir() -> None:
    assert config.user_data_dir() == Path.home() / "Library" / "Application Support" / "Precursor"


def test_env_still_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An explicit override must beat both defaults — conftest relies on it."""
    monkeypatch.setenv("PRECURSOR_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    assert config.get_settings().data_dir == str(tmp_path)


def test_derived_paths_hang_off_the_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PRECURSOR_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    settings = config.get_settings()
    assert Path(settings.logs_dir) == tmp_path / "logs"
    assert Path(settings.runtime_state_file) == tmp_path / "runtime.json"
