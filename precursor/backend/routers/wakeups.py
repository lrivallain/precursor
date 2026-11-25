"""Wakeups router: the conversations a model paused (``services/wakeups``).

The sidebars read ``GET /api/wakeups`` to mark paused conversations; a paused
conversation's banner can wake it up now or cancel the wakeup.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.lookups import get_or_404
from precursor.backend.models import Wakeup
from precursor.backend.schemas.wakeup import WakeupRead
from precursor.backend.services import wakeups as svc
from precursor.backend.services.wakeup_ticker import get_wakeup_ticker

router = APIRouter(prefix="/api/wakeups", tags=["wakeups"])


@router.get("", response_model=list[WakeupRead])
async def list_wakeups(session: AsyncSession = Depends(get_session)) -> list[WakeupRead]:
    """Every paused conversation, soonest wakeup first."""
    return await svc.list_pending(session)


@router.post("/{wakeup_id}/wake", status_code=status.HTTP_204_NO_CONTENT)
async def wake_now(wakeup_id: int, session: AsyncSession = Depends(get_session)) -> None:
    """Wake the conversation up now instead of at its scheduled time."""
    wakeup = await get_or_404(session, Wakeup, wakeup_id, "Wakeup not found")
    await svc.wake_now(session, wakeup)
    await get_wakeup_ticker().nudge()


@router.delete("/{wakeup_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_wakeup(wakeup_id: int, session: AsyncSession = Depends(get_session)) -> None:
    """Cancel a wakeup: the conversation stays as it is, no longer paused."""
    wakeup = await get_or_404(session, Wakeup, wakeup_id, "Wakeup not found")
    await svc.cancel(session, *svc.target_of(wakeup))
