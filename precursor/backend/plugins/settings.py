"""Per-plugin settings — a namespaced JSON blob each plugin owns.

A plugin needs somewhere to keep configuration without adding fields to core's
settings schema, which would couple every plugin to the host's release cycle and
let one plugin's key collide with another's. So each plugin gets one opaque JSON
object, stored in the same ``AppSetting`` table under ``plugin.<id>``.

Core never interprets the contents. It only guarantees the namespace, so a
plugin's frontend and its backend read exactly the same thing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import AppSetting

logger = logging.getLogger(__name__)

#: Prefix that keeps a plugin's blob from colliding with a core setting. A dot
#: is deliberate: core's own keys are bare identifiers, so the two can't overlap.
KEY_PREFIX = "plugin."


def settings_key(plugin_id: str) -> str:
    return f"{KEY_PREFIX}{plugin_id}"


async def read_settings(session: AsyncSession, plugin_id: str) -> dict[str, Any]:
    """This plugin's stored settings, or ``{}``.

    Never raises on malformed stored data: a plugin downgrade that left an
    unreadable blob behind should present as "unconfigured", not as a 500 on
    every request that touches it.
    """
    row = await session.get(AppSetting, settings_key(plugin_id))
    if row is None:
        return {}
    try:
        value = json.loads(row.value)
    except (TypeError, ValueError):
        logger.warning("Ignoring unreadable settings for plugin %s", plugin_id)
        return {}
    return value if isinstance(value, dict) else {}


async def write_settings(
    session: AsyncSession, plugin_id: str, values: dict[str, Any]
) -> dict[str, Any]:
    """Replace this plugin's settings wholesale and commit."""
    encoded = json.dumps(values)
    key = settings_key(plugin_id)
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=encoded))
    else:
        row.value = encoded
    await session.commit()
    return values


async def get_settings(plugin_id: str) -> dict[str, Any]:
    """Read a plugin's settings from anywhere — including an MCP subprocess.

    Opens its own session, so a plugin's tool server or a background task can
    reach its configuration without being handed one.
    """
    async with SessionLocal() as session:
        return await read_settings(session, plugin_id)
