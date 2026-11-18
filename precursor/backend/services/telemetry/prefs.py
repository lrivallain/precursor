"""Whether telemetry may run, what the user chose, and the state it keeps.

Two switches, both off until the user turns them on: the daily heartbeat
(``telemetry_enabled``) and error reports (``telemetry_errors_enabled``, which
needs the first). A stored ``telemetry_enabled`` of either value means the user
answered the in-app invitation, which then stops showing.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import get_settings
from precursor.backend.services.app_settings import _get_db_value
from precursor.backend.services.setting_rows import delete_setting, store_setting
from precursor.backend.services.telemetry.appinsights import Target, parse_connection_string

ENABLED_KEY = "telemetry_enabled"
ERRORS_KEY = "telemetry_errors_enabled"
INSTALL_ID_KEY = "telemetry_install_id"
LAST_HEARTBEAT_KEY = "telemetry_last_heartbeat"

_OFF_VALUES = {"0", "false", "no", "off"}


def blocked_reason() -> str | None:
    """Why this process sends nothing whatever the user chose, else None."""
    if get_settings().telemetry.strip().lower() in _OFF_VALUES:
        return "Turned off by PRECURSOR_TELEMETRY."
    if os.environ.get("DO_NOT_TRACK", "").strip().lower() not in ("", *_OFF_VALUES):
        return "Turned off by DO_NOT_TRACK."
    # CI runs would count as users and drown the real ones.
    if os.environ.get("CI", "").strip().lower() not in ("", *_OFF_VALUES):
        return "Turned off in CI."
    if target() is None:
        return "This build has no telemetry endpoint."
    return None


def target() -> Target | None:
    return parse_connection_string(get_settings().telemetry_connection_string)


@dataclass(frozen=True)
class Choice:
    decided: bool
    enabled: bool
    errors_enabled: bool


async def load_choice(session: AsyncSession) -> Choice:
    enabled = await _get_db_value(session, ENABLED_KEY)
    errors = await _get_db_value(session, ERRORS_KEY)
    return Choice(
        decided=isinstance(enabled, bool),
        enabled=enabled is True,
        errors_enabled=enabled is True and errors is True,
    )


async def install_id(session: AsyncSession) -> str:
    """The random id that lets distinct installs be counted; minted on first use."""
    existing = await _get_db_value(session, INSTALL_ID_KEY)
    if isinstance(existing, str) and existing:
        return existing
    minted = str(uuid.uuid4())
    await store_setting(session, INSTALL_ID_KEY, minted)
    return minted


async def forget_install(session: AsyncSession) -> None:
    """Drop the id, so turning telemetry back on later looks like a new install."""
    for key in (INSTALL_ID_KEY, LAST_HEARTBEAT_KEY):
        await delete_setting(session, key)


async def last_heartbeat(session: AsyncSession) -> str | None:
    value = await _get_db_value(session, LAST_HEARTBEAT_KEY)
    return value if isinstance(value, str) else None


async def record_heartbeat(session: AsyncSession, day: str) -> None:
    await store_setting(session, LAST_HEARTBEAT_KEY, day)
