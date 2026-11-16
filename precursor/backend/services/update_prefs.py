"""The user's update preferences: channel, background checks, notifications.

Set from the Settings panel but read by the tray, a separate process that runs
whether or not the app is up and never opens the database. So they live in a
small JSON file in the data directory, next to ``runtime.json`` and
``tray.json``, rather than in the ``AppSetting`` table.

The ``PRECURSOR_UPDATE_CHANNEL`` and ``PRECURSOR_UPDATE_NOTIFY`` environment
variables are the factory defaults until the user saves a choice here.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

from precursor.backend.config import get_settings

Channel = Literal["stable", "nightly"]
CHANNELS: tuple[Channel, ...] = ("stable", "nightly")

_FILENAME = "update-prefs.json"


@dataclass(frozen=True)
class UpdatePrefs:
    # ``None`` follows the running build: a dev version tracks nightly, a
    # tagged one tracks stable. Only reachable through an empty
    # ``PRECURSOR_UPDATE_CHANNEL``.
    channel: Channel | None = "stable"
    # Background checks (the tray's half-hourly poll). A manual "Check for
    # updates" and ``precursor service update`` still work when this is off.
    checks_enabled: bool = True
    # Whether a background check that finds a new build posts an OS
    # notification. Off by default: the tray menu says so either way, and a
    # notification mid-meeting is the interruption this setting exists to avoid.
    notifications_enabled: bool = False


def _path() -> Path:
    return Path(get_settings().data_dir) / _FILENAME


def _as_channel(raw: Any) -> Channel | None:
    value = raw.strip().lower() if isinstance(raw, str) else ""
    return value if value in CHANNELS else None


def _env_defaults() -> UpdatePrefs:
    cfg = get_settings()
    return UpdatePrefs(
        channel=_as_channel(cfg.update_channel),
        notifications_enabled=cfg.update_notify.strip().lower() not in ("", "off"),
    )


def load() -> UpdatePrefs:
    """The saved preferences over the environment defaults."""
    prefs = _env_defaults()
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return prefs
    if not isinstance(raw, dict):
        return prefs
    # A saved null means "follow the running build"; anything unknown is ignored.
    if (raw.get("channel") is None and "channel" in raw) or _as_channel(raw.get("channel")):
        prefs = replace(prefs, channel=_as_channel(raw["channel"]))
    for key in ("checks_enabled", "notifications_enabled"):
        if isinstance(raw.get(key), bool):
            prefs = replace(prefs, **{key: raw[key]})
    return prefs


def save(**changes: Any) -> UpdatePrefs:
    """Merge ``changes`` into the saved preferences and write them.

    Written to a temporary file and renamed, so the tray never reads half a file.
    """
    prefs = replace(load(), **changes)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(prefs)), encoding="utf-8")
    try:
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
    return prefs
