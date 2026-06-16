"""Speech-to-text router — brokers short-lived Azure Speech tokens to the SPA.

The browser performs the actual recognition against Azure directly; this only
hands out a time-limited token so the subscription key stays server-side.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.services.app_settings import (
    resolve_azure_speech_key,
    resolve_azure_speech_region,
)
from precursor.backend.services.stt import mint_speech_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stt", tags=["stt"])


class SpeechToken(BaseModel):
    token: str
    region: str


@router.get("/token", response_model=SpeechToken)
async def get_speech_token(session: AsyncSession = Depends(get_session)) -> SpeechToken:
    """Return a short-lived Azure Speech authorization token + region."""
    key = await resolve_azure_speech_key(session)
    region = await resolve_azure_speech_region(session)
    if not key or not region:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Azure Speech is not configured. Set the key and region in Settings.",
        )
    try:
        token = await mint_speech_token(key, region)
    except httpx.HTTPStatusError as exc:
        # Surface a clean message; never echo the key. 401/403 => bad key/region.
        logger.warning("Azure Speech token request failed: %s", exc.response.status_code)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Azure rejected the Speech credentials ({exc.response.status_code}).",
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("Azure Speech token request error: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not reach Azure Speech.") from exc
    return SpeechToken(token=token, region=region)
