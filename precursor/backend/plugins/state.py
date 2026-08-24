"""Which installed plugins are switched on.

The flag lives in the ``plugins_disabled`` app setting (a list of plugin ids)
rather than a dedicated table: it is a small, sparse opt-*out*, so a plugin that
has never been touched is on, and uninstalling one leaves no orphan row behind.

The set is mirrored in memory because it gates every request to a plugin route —
a database round-trip per call would be absurd. :func:`refresh` reloads it (at
startup and after a toggle) and is the only writer of that cache.
"""

from __future__ import annotations

import json
import logging

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import AppSetting

logger = logging.getLogger(__name__)

SETTING_KEY = "plugins_disabled"

# Ids the user has switched off. Empty until refresh() runs, so a plugin route
# hit before startup finishes is allowed rather than spuriously 404ing.
_disabled: set[str] = set()


def is_enabled(plugin_id: str) -> bool:
    return plugin_id not in _disabled


def disabled_ids() -> set[str]:
    return set(_disabled)


async def load_disabled(session: AsyncSession) -> set[str]:
    row = await session.get(AppSetting, SETTING_KEY)
    if row is None:
        return set()
    try:
        value = json.loads(row.value)
    except (TypeError, ValueError):
        return set()
    return {str(x) for x in value} if isinstance(value, list) else set()


async def refresh() -> set[str]:
    """Reload the in-memory cache from the database."""
    global _disabled
    async with SessionLocal() as session:
        _disabled = await load_disabled(session)
    return set(_disabled)


async def set_enabled(session: AsyncSession, plugin_id: str, enabled: bool) -> set[str]:
    """Switch a plugin on or off and refresh the cache. Returns the disabled set."""
    global _disabled
    current = await load_disabled(session)
    if enabled:
        current.discard(plugin_id)
    else:
        current.add(plugin_id)
    encoded = json.dumps(sorted(current))
    row = await session.get(AppSetting, SETTING_KEY)
    if row is None:
        session.add(AppSetting(key=SETTING_KEY, value=encoded))
    else:
        row.value = encoded
    await session.commit()
    _disabled = current
    return set(_disabled)


def require_plugin_enabled(plugin_id: str):  # type: ignore[no-untyped-def]
    """Build the route dependency that gates one plugin's endpoints.

    A disabled plugin's routes stay mounted but answer 404, so switching it off
    genuinely removes its API rather than only hiding its UI.
    """

    async def _guard() -> None:
        if not is_enabled(plugin_id):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"The '{plugin_id}' plugin is disabled.",
            )

    return _guard
