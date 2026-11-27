"""Remote access to the UI through a tunnel (issue #521)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RemoteSessionRead(BaseModel):
    id: str
    label: str
    created_at: datetime
    last_used_at: datetime


class RemoteSessionRename(BaseModel):
    label: str = Field(min_length=1, max_length=120)


class RemoteAccessRead(BaseModel):
    enabled: bool
    sessions: list[RemoteSessionRead]
    # When a pairing code is waiting to be used, until when.
    pairing_expires_at: datetime | None = None


class RemoteAccessUpdate(BaseModel):
    enabled: bool


class PairingCodeRead(BaseModel):
    # Path (no origin) of the pairing link.
    path: str
    expires_at: datetime
    # The full link and its QR code, when the tunnel is up and its address known.
    url: str | None = None
    qr_svg: str | None = None


class RemoteContextRead(BaseModel):
    remote: bool = Field(description="True when this request came through a tunnel or proxy.")
    # The absolute origin tools use in links to the app's pages (loopback bind),
    # so the UI can tell those links from other local services.
    app_origin: str = ""


class TunnelStatusRead(BaseModel):
    state: Literal["missing_cli", "signed_out", "stopped", "starting", "active", "error"]
    url: str | None = None
    error: str | None = None


class TunnelUpdate(BaseModel):
    running: bool
    # On stop: also sign out every paired device.
    revoke_sessions: bool = False


class RemoteHealthRead(BaseModel):
    ok: bool = True
