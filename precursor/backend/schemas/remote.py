"""Remote access to the UI through a tunnel (issue #521)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RemoteSessionRead(BaseModel):
    id: str
    label: str
    created_at: datetime
    last_used_at: datetime


class RemoteAccessRead(BaseModel):
    enabled: bool
    sessions: list[RemoteSessionRead]
    # When a pairing code is waiting to be used, until when.
    pairing_expires_at: datetime | None = None


class RemoteAccessUpdate(BaseModel):
    enabled: bool


class PairingCodeRead(BaseModel):
    # Path (no origin) of the link the QR code carries; the tunnel's address
    # goes in front of it.
    path: str
    expires_at: datetime


class RemoteContextRead(BaseModel):
    remote: bool = Field(description="True when this request came through a tunnel or proxy.")
