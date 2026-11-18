"""Anonymous usage data: its status and a preview of what is sent.

The choice itself is saved through ``PUT /api/settings`` like every setting.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.schemas.telemetry import TelemetryStatus
from precursor.backend.services import telemetry

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])


@router.get("", response_model=TelemetryStatus)
async def read_telemetry(session: AsyncSession = Depends(get_session)) -> TelemetryStatus:
    return TelemetryStatus(**asdict(await telemetry.status(session)))
