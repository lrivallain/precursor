"""Application-settings router — runtime-editable preferences stored in DB."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import get_settings
from precursor.backend.db import get_session
from precursor.backend.models import AppSetting
from precursor.backend.schemas import SettingsPayload, SettingsRead

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Keys that contain secrets — never echoed back, only their presence is reported.
_SECRET_KEYS = {"api_keys"}


async def _load_all(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(select(AppSetting))
    return {row.key: json.loads(row.value) for row in result.scalars().all()}


async def _upsert(session: AsyncSession, key: str, value: Any) -> None:
    existing = await session.get(AppSetting, key)
    encoded = json.dumps(value)
    if existing is None:
        session.add(AppSetting(key=key, value=encoded))
    else:
        existing.value = encoded


def _as_read(data: dict[str, Any]) -> SettingsRead:
    defaults = get_settings()
    api_keys = data.get("api_keys") or {}
    return SettingsRead(
        theme=data.get("theme", "system"),
        llm_model=data.get("llm_model", defaults.llm_model),
        github_repo=data.get("github_repo", defaults.github_repo),
        mcp_enabled=data.get("mcp_enabled", {}),
        mcp_servers=data.get("mcp_servers", {}),
        api_keys_present={k: bool(v) for k, v in api_keys.items()},
    )


@router.get("", response_model=SettingsRead)
async def read_settings(session: AsyncSession = Depends(get_session)) -> SettingsRead:
    return _as_read(await _load_all(session))


@router.put("", response_model=SettingsRead)
async def update_settings(
    payload: SettingsPayload,
    session: AsyncSession = Depends(get_session),
) -> SettingsRead:
    data = payload.model_dump(exclude_unset=True)

    # Merge api_keys instead of replacing — clients may PATCH a single key.
    if "api_keys" in data:
        current = await _load_all(session)
        merged = {**(current.get("api_keys") or {}), **data["api_keys"]}
        data["api_keys"] = {k: v for k, v in merged.items() if v}

    for key, value in data.items():
        await _upsert(session, key, value)
    await session.commit()
    return _as_read(await _load_all(session))
