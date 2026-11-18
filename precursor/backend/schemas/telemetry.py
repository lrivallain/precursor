"""What the settings tab and the invitation banner show about usage data."""

from __future__ import annotations

from pydantic import BaseModel


class TelemetryStatus(BaseModel):
    # False when nothing can be sent whatever the user picks (no endpoint in
    # this build, PRECURSOR_TELEMETRY=off, DO_NOT_TRACK, CI); the reason says which.
    available: bool
    unavailable_reason: str | None = None
    # Whether the user has answered the invitation (yes or no).
    decided: bool
    enabled: bool
    errors_enabled: bool
    # The heartbeat this install would send, as the exact JSON body.
    preview: str
