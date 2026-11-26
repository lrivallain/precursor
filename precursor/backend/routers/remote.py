"""Remote access to the UI: switch, pairing and paired devices (issue #521).

Everything here is local-only except ``pair`` (the link the phone opens) and
``context`` (so the UI knows where it runs).
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.routers.deps import require_local
from precursor.backend.schemas.remote import (
    PairingCodeRead,
    RemoteAccessRead,
    RemoteAccessUpdate,
    RemoteContextRead,
    RemoteSessionRead,
)
from precursor.backend.services.remote_access import is_remote
from precursor.backend.services.remote_access import sessions as store
from precursor.backend.services.remote_access.devices import sign_out_all_devices
from precursor.backend.services.remote_access.gate import PAIR_PATH, session_cookie
from precursor.backend.services.remote_access.pairing import pairing_codes

router = APIRouter(prefix="/api/remote", tags=["remote"], dependencies=[Depends(require_local)])
public_router = APIRouter(prefix="/api/remote", tags=["remote"])

_PAIR_FAILED = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Precursor</title></head>
<body style="font-family:system-ui;max-width:32rem;margin:3rem auto;padding:0 1rem">
<h1>This link can't be used</h1><p>It has expired or was already used. On your computer,
open <strong>Mobile access</strong> again for a new QR code.</p></body></html>"""


def _read(state: store.RemoteAccessState) -> RemoteAccessRead:
    return RemoteAccessRead(
        enabled=state.enabled,
        sessions=[
            RemoteSessionRead(
                id=s.id,
                label=s.label,
                created_at=datetime.fromisoformat(s.created_at),
                last_used_at=datetime.fromisoformat(s.last_used_at),
            )
            for s in state.sessions
        ],
        pairing_expires_at=pairing_codes.pending_until() if state.enabled else None,
    )


@router.get("/access", response_model=RemoteAccessRead)
async def get_access(session: AsyncSession = Depends(get_session)) -> RemoteAccessRead:
    return _read(await store.load(session))


@router.put("/access", response_model=RemoteAccessRead)
async def update_access(body: RemoteAccessUpdate) -> RemoteAccessRead:
    def switch(state: store.RemoteAccessState) -> None:
        state.enabled = body.enabled

    state, _ = await store.update(switch)
    if not body.enabled:
        pairing_codes.cancel()
    return _read(state)


@router.post("/pairing", response_model=PairingCodeRead)
async def mint_pairing(session: AsyncSession = Depends(get_session)) -> PairingCodeRead:
    if not (await store.load(session)).enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Remote access is off.")
    code, expires_at = pairing_codes.mint()
    return PairingCodeRead(path=f"{PAIR_PATH}?code={quote(code)}", expires_at=expires_at)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(session_id: str) -> None:
    _state, revoked = await store.update(lambda state: store.revoke(state, session_id))
    if not revoked:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")


@router.delete("/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_all_sessions() -> None:
    await sign_out_all_devices()


@public_router.get("/context", response_model=RemoteContextRead)
async def get_context(request: Request) -> RemoteContextRead:
    return RemoteContextRead(remote=is_remote(request.scope))


@public_router.get("/pair", response_model=None, include_in_schema=False)
async def pair(
    request: Request, code: str = "", session: AsyncSession = Depends(get_session)
) -> Response:
    state = await store.load(session)
    # Only a device reaching us through the tunnel pairs: opening the link on
    # the PC itself would burn the code for nothing.
    if not state.enabled or not is_remote(request.scope):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    if not pairing_codes.consume(code):
        return HTMLResponse(_PAIR_FAILED, status_code=status.HTTP_401_UNAUTHORIZED)
    label = request.headers.get("user-agent", "")
    _state, token = await store.update(lambda fresh: store.issue(fresh, label))
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.headers.append("set-cookie", session_cookie(token))
    return response
