"""Speech-to-text router — brokers short-lived Azure Speech tokens to the SPA.

The browser performs the actual recognition against Azure directly; this only
hands out a time-limited token, minted from the subscription key or the Azure
CLI sign-in, so no long-lived credential reaches the browser.
"""

from __future__ import annotations

import logging
from dataclasses import replace

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.schemas.settings import SpeechAuth
from precursor.backend.services.azure_cli import AzureCliError
from precursor.backend.services.speech_settings import (
    SpeechConfig,
    resolve_azure_speech_language,
    resolve_speech_config,
)
from precursor.backend.services.stt import mint_token_for

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
    # Optional: the unsaved form values; omitted fields use the saved ones.
    auth: SpeechAuth | None = None
    tenant: str | None = None
    resource_id: str | None = None


class SpeechTestResult(BaseModel):
    ok: bool
    detail: str | None = None


def _not_ready_message(config: SpeechConfig) -> str:
    if not config.endpoint:
        return "Provide the endpoint URL."
    return "Provide the subscription key."


@router.get("/token", response_model=SpeechToken)
async def get_speech_token(session: AsyncSession = Depends(get_session)) -> SpeechToken:
    """Return a short-lived Azure Speech authorization token + endpoint."""
    config = await resolve_speech_config(session)
    if not config.ready:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Azure Speech is not configured. Set the endpoint and credentials in Settings.",
        )
    try:
        token = await mint_token_for(config)
    except AzureCliError as exc:
        logger.warning("Azure Speech sign-in with the Azure CLI failed: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
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
    return SpeechToken(token=token, endpoint=config.endpoint, language=language)


@router.post("/test", response_model=SpeechTestResult)
async def test_speech_connection(
    payload: SpeechTestRequest,
    session: AsyncSession = Depends(get_session),
) -> SpeechTestResult:
    """Validate the form's endpoint and credential by minting a token (without saving)."""
    saved = await resolve_speech_config(session)
    config = replace(
        saved,
        endpoint=payload.endpoint.strip(),
        auth=payload.auth or saved.auth,
        key=(payload.key or "").strip() or saved.key,
        tenant=saved.tenant if payload.tenant is None else payload.tenant.strip(),
        resource_id=saved.resource_id if payload.resource_id is None else payload.resource_id,
    )
    if not config.ready:
        return SpeechTestResult(ok=False, detail=_not_ready_message(config))
    try:
        await mint_token_for(config)
    except AzureCliError as exc:
        return SpeechTestResult(ok=False, detail=str(exc))
    except httpx.HTTPStatusError as exc:
        return SpeechTestResult(
            ok=False, detail=f"Azure rejected the credentials ({exc.response.status_code})."
        )
    except httpx.HTTPError:
        return SpeechTestResult(ok=False, detail="Could not reach the endpoint.")
    if config.auth == "azure_cli":
        return SpeechTestResult(
            ok=True, detail="Signed in with the Azure CLI and found the Speech resource."
        )
    return SpeechTestResult(ok=True, detail="Connection OK.")
