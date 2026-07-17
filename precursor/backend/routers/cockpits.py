"""Cockpit endpoints — register, launch, and reverse-proxy local webapps.

A cockpit is a locally-run web application the user registers with a run
command and a loopback port. This router provides:

* CRUD over the cockpit *definitions* (``services`` live in the DB).
* ``start`` / ``stop`` / ``restart`` / ``status`` / ``logs`` over the ephemeral
  process, delegated to the :class:`CockpitManager`.
* A **reverse proxy** (``/proxy/…``) that forwards to the running cockpit on
  loopback, strips framing headers (``X-Frame-Options`` / CSP
  ``frame-ancestors``) so the app can be embedded in an iframe, and rewrites
  root-relative asset URLs in HTML so they resolve back through the proxy.

Because starting a cockpit runs an arbitrary command on the host, the whole
feature is disabled unless the backend is bound to loopback and
``cockpits_enabled`` is set. The frontend also offers an "open in new tab"
fallback (straight to ``http://localhost:<port>``) for apps the proxy can't
fully rewrite (e.g. ones that build URLs in JavaScript or rely on websockets).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import get_settings
from precursor.backend.db import get_session
from precursor.backend.models import Cockpit
from precursor.backend.schemas import (
    CockpitCreate,
    CockpitLogs,
    CockpitRead,
    CockpitStatus,
    CockpitUpdate,
)
from precursor.backend.services.cockpits import get_cockpit_manager
from precursor.backend.services.slugs import slugify

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cockpits", tags=["cockpits"])

# Response headers that must never be forwarded verbatim: hop-by-hop headers,
# plus anything that would prevent iframe embedding.
_STRIP_RESPONSE_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "x-frame-options",
    "content-security-policy",
    "content-security-policy-report-only",
}
_STRIP_REQUEST_HEADERS = {
    "host",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    # We re-derive the target's content length from the forwarded body.
    "content-length",
}

# Rewrite root-relative src/href/action (but not protocol-relative "//host").
_ROOT_REL_RE = re.compile(rb'((?:src|href|action)\s*=\s*["\'])/(?!/)')


def _guard_enabled() -> None:
    """Reject all cockpit routes unless enabled on a loopback bind."""
    from precursor.backend.services.mcp.precursor_server import is_loopback_host

    cfg = get_settings()
    if not cfg.cockpits_enabled or not is_loopback_host(cfg.host):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


async def _get(cockpit_id: int, session: AsyncSession) -> Cockpit:
    cockpit = await session.get(Cockpit, cockpit_id)
    if cockpit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cockpit not found")
    return cockpit


async def _allocate_slug(session: AsyncSession, base: str, exclude_id: int | None = None) -> str:
    base = base or "cockpit"
    candidate, n = base, 2
    while True:
        stmt = select(Cockpit.id).where(Cockpit.slug == candidate)
        if exclude_id is not None:
            stmt = stmt.where(Cockpit.id != exclude_id)
        if (await session.execute(stmt)).first() is None:
            return candidate
        candidate, n = f"{base}-{n}", n + 1


def _env_dict(cockpit: Cockpit) -> dict[str, str]:
    if not cockpit.env:
        return {}
    try:
        parsed = json.loads(cockpit.env)
    except json.JSONDecodeError:
        return {}
    return {str(k): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}


def _to_read(cockpit: Cockpit) -> CockpitRead:
    read = CockpitRead.model_validate(cockpit)
    read.status = get_cockpit_manager().get_status(cockpit.id)
    return read


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------


@router.get("", response_model=list[CockpitRead])
async def list_cockpits(session: AsyncSession = Depends(get_session)) -> list[CockpitRead]:
    _guard_enabled()
    result = await session.execute(select(Cockpit).order_by(Cockpit.created_at.desc()))
    return [_to_read(c) for c in result.scalars().all()]


@router.post("", response_model=CockpitRead, status_code=status.HTTP_201_CREATED)
async def create_cockpit(
    payload: CockpitCreate, session: AsyncSession = Depends(get_session)
) -> CockpitRead:
    _guard_enabled()
    slug = await _allocate_slug(session, slugify(payload.slug or payload.name))
    cockpit = Cockpit(
        name=payload.name.strip(),
        slug=slug,
        description=(payload.description or "").strip() or None,
        command=payload.command.strip(),
        cwd=(payload.cwd or "").strip() or None,
        port=payload.port,
        env=payload.env,
    )
    session.add(cockpit)
    await session.commit()
    await session.refresh(cockpit)
    return _to_read(cockpit)


@router.get("/{cockpit_id}", response_model=CockpitRead)
async def get_cockpit(cockpit_id: int, session: AsyncSession = Depends(get_session)) -> CockpitRead:
    _guard_enabled()
    return _to_read(await _get(cockpit_id, session))


@router.patch("/{cockpit_id}", response_model=CockpitRead)
async def update_cockpit(
    cockpit_id: int, payload: CockpitUpdate, session: AsyncSession = Depends(get_session)
) -> CockpitRead:
    _guard_enabled()
    cockpit = await _get(cockpit_id, session)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        cockpit.name = data["name"].strip()
    if "command" in data and data["command"] is not None:
        cockpit.command = data["command"].strip()
    if "port" in data and data["port"] is not None:
        cockpit.port = data["port"]
    if "description" in data:
        cockpit.description = (data["description"] or "").strip() or None
    if "cwd" in data:
        cockpit.cwd = (data["cwd"] or "").strip() or None
    if "env" in data:
        cockpit.env = data["env"]
    await session.commit()
    await session.refresh(cockpit)
    return _to_read(cockpit)


@router.delete("/{cockpit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cockpit(cockpit_id: int, session: AsyncSession = Depends(get_session)) -> None:
    _guard_enabled()
    cockpit = await _get(cockpit_id, session)
    await get_cockpit_manager().stop(cockpit_id)
    await session.delete(cockpit)
    await session.commit()


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


@router.post("/{cockpit_id}/start", response_model=CockpitStatus)
async def start_cockpit(
    cockpit_id: int, session: AsyncSession = Depends(get_session)
) -> CockpitStatus:
    _guard_enabled()
    cockpit = await _get(cockpit_id, session)
    return await get_cockpit_manager().start(
        cockpit_id=cockpit.id,
        command=cockpit.command,
        port=cockpit.port,
        cwd=cockpit.cwd,
        env=_env_dict(cockpit),
    )


@router.post("/{cockpit_id}/restart", response_model=CockpitStatus)
async def restart_cockpit(
    cockpit_id: int, session: AsyncSession = Depends(get_session)
) -> CockpitStatus:
    _guard_enabled()
    cockpit = await _get(cockpit_id, session)
    return await get_cockpit_manager().restart(
        cockpit_id=cockpit.id,
        command=cockpit.command,
        port=cockpit.port,
        cwd=cockpit.cwd,
        env=_env_dict(cockpit),
    )


@router.post("/{cockpit_id}/stop", response_model=CockpitStatus)
async def stop_cockpit(
    cockpit_id: int, session: AsyncSession = Depends(get_session)
) -> CockpitStatus:
    _guard_enabled()
    await _get(cockpit_id, session)
    return await get_cockpit_manager().stop(cockpit_id)


@router.get("/{cockpit_id}/status", response_model=CockpitStatus)
async def cockpit_status(
    cockpit_id: int, session: AsyncSession = Depends(get_session)
) -> CockpitStatus:
    _guard_enabled()
    await _get(cockpit_id, session)
    return get_cockpit_manager().get_status(cockpit_id)


@router.get("/{cockpit_id}/logs", response_model=CockpitLogs)
async def cockpit_logs(
    cockpit_id: int, session: AsyncSession = Depends(get_session)
) -> CockpitLogs:
    _guard_enabled()
    await _get(cockpit_id, session)
    return CockpitLogs(logs=get_cockpit_manager().get_logs(cockpit_id))


# --------------------------------------------------------------------------
# Reverse proxy
# --------------------------------------------------------------------------

_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


@router.api_route("/{cockpit_id}/proxy", methods=_PROXY_METHODS, include_in_schema=False)
@router.api_route(
    "/{cockpit_id}/proxy/{path:path}", methods=_PROXY_METHODS, include_in_schema=False
)
async def proxy(cockpit_id: int, request: Request, path: str = "") -> Response:
    _guard_enabled()
    port = get_cockpit_manager().running_port(cockpit_id)
    if port is None:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Cockpit is not running — start it first.",
        )

    prefix = f"/api/cockpits/{cockpit_id}/proxy/"
    target = f"http://127.0.0.1:{port}/{path}"
    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _STRIP_REQUEST_HEADERS
    }
    body = await request.body()

    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0), follow_redirects=False)
    try:
        upstream = client.build_request(
            request.method,
            target,
            params=dict(request.query_params),
            headers=fwd_headers,
            content=body or None,
        )
        resp = await client.send(upstream, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Cockpit did not respond: {exc}") from exc

    out_headers = _response_headers(resp, prefix)
    content_type = resp.headers.get("content-type", "")

    if _is_html(content_type):
        raw = await resp.aread()
        await resp.aclose()
        await client.aclose()
        rewritten = _ROOT_REL_RE.sub(rb"\1" + prefix.encode() + rb"", raw)
        out_headers.pop("content-length", None)
        out_headers.pop("content-encoding", None)
        return Response(
            content=rewritten,
            status_code=resp.status_code,
            headers=out_headers,
            media_type=content_type or None,
        )

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        stream(),
        status_code=resp.status_code,
        headers=out_headers,
        media_type=content_type or None,
    )


def _is_html(content_type: str) -> bool:
    return "text/html" in content_type.lower()


def _response_headers(resp: httpx.Response, prefix: str) -> dict[str, str]:
    """Copy upstream headers minus hop-by-hop/framing, rewriting redirects."""
    headers: dict[str, str] = {}
    for key, value in resp.headers.items():
        low = key.lower()
        if low in _STRIP_RESPONSE_HEADERS:
            continue
        if low == "location":
            value = _rewrite_location(value, prefix)
        headers[key] = value
    return headers


def _rewrite_location(location: str, prefix: str) -> str:
    """Keep redirects inside the proxy for root-relative/loopback targets."""
    if location.startswith("//"):
        return location
    if location.startswith("/"):
        return prefix.rstrip("/") + location
    for scheme in ("http://127.0.0.1", "http://localhost"):
        if location.startswith(scheme):
            rest = location[len(scheme) :]
            slash = rest.find("/")
            path = rest[slash:] if slash != -1 else "/"
            return prefix.rstrip("/") + path
    return location
