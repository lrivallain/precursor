"""Writing one ``AppSetting`` row (JSON-encoded), for code that stores state there."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import AppSetting


async def store_setting(session: AsyncSession, key: str, value: Any) -> None:
    """Insert or replace ``key``; the caller commits."""
    row = await session.get(AppSetting, key)
    encoded = json.dumps(value)
    if row is None:
        session.add(AppSetting(key=key, value=encoded))
    else:
        row.value = encoded


async def delete_setting(session: AsyncSession, key: str) -> None:
    row = await session.get(AppSetting, key)
    if row is not None:
        await session.delete(row)
