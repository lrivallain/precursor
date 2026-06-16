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
    resolve_azure_speech_endpoint,
    resolve_azure_speech_key,
    resolve_azure_speech_language,
)
from precursor.backend.services.stt import mint_speech_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stt", tags=["stt"])


class SpeechToken(BaseModel):
    token: str
    endpoint: str
    language: str


class SpeechTestRequest(BaseModel):
    endpoint: str
    # Optional: when omitted/blank, the already-saved key is used (so the user
    # can test without re-typing a configured secret).
    key: str | None = None


class SpeechTestResult(BaseModel):
    ok: bool
    detail: str | None = None


@router.get("/token", response_model=SpeechToken)
async def get_speech_token(session: AsyncSession = Depends(get_session)) -> SpeechToken:
    """Return a short-lived Azure Speech authorization token + endpoint."""
    key = await resolve_azure_speech_key(session)
    endpoint = await resolve_azure_speech_endpoint(session)
    if not key or not endpoint:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Azure Speech is not configured. Set the key and endpoint in Settings.",
        )
    try:
        token = await mint_speech_token(key, endpoint)
    except httpx.HTTPStatusError as exc:
        # Surface a clean message; never echo the key. 401/403 => bad key/endpoint.
        logger.warning("Azure Speech token request failed: %s", exc.response.status_code)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Azure rejected the Speech credentials ({exc.response.status_code}).",
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("Azure Speech token request error: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not reach Azure Speech.") from exc
    language = await resolve_azure_speech_language(session)
    return SpeechToken(token=token, endpoint=endpoint, language=language)


@router.post("/test", response_model=SpeechTestResult)
async def test_speech_connection(
    payload: SpeechTestRequest,
    session: AsyncSession = Depends(get_session),
) -> SpeechTestResult:
    """Validate an endpoint + key by minting a token (without saving them)."""
    endpoint = payload.endpoint.strip()
    key = (payload.key or "").strip() or await resolve_azure_speech_key(session)
    if not endpoint:
        return SpeechTestResult(ok=False, detail="Provide the endpoint URL.")
    if not key:
        return SpeechTestResult(ok=False, detail="Provide the subscription key.")
    try:
        await mint_speech_token(key, endpoint)
    except httpx.HTTPStatusError as exc:
        return SpeechTestResult(
            ok=False, detail=f"Azure rejected the credentials ({exc.response.status_code})."
        )
    except httpx.HTTPError:
        return SpeechTestResult(ok=False, detail="Could not reach the endpoint.")
    return SpeechTestResult(ok=True, detail="Connection OK.")
