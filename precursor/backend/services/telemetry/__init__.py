"""Anonymous, opt-in usage data sent to the project's Application Insights.

``appinsights`` builds and posts envelopes, ``heartbeat`` and ``errors`` decide
what goes in them, ``prefs`` holds the user's choice and the install id, and
``ticker`` sends them. ``status`` is what the settings UI and the invitation
banner read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.services.telemetry import heartbeat, prefs
from precursor.backend.services.telemetry.appinsights import Target

# Shown in the preview in place of the real id, which is only minted on opt-in.
PREVIEW_INSTALL_ID = "00000000-0000-4000-8000-000000000000"
# A build without an endpoint still shows what it would send.
_PREVIEW_TARGET = Target("<instrumentation-key>", "https://dc.services.visualstudio.com/")


@dataclass(frozen=True)
class Status:
    available: bool
    unavailable_reason: str | None
    decided: bool
    enabled: bool
    errors_enabled: bool
    preview: str


async def status(session: AsyncSession) -> Status:
    choice = await prefs.load_choice(session)
    reason = prefs.blocked_reason()
    sample = await heartbeat.build(session, prefs.target() or _PREVIEW_TARGET, PREVIEW_INSTALL_ID)
    return Status(
        available=reason is None,
        unavailable_reason=reason,
        decided=choice.decided,
        enabled=choice.enabled,
        errors_enabled=choice.errors_enabled,
        preview=json.dumps(sample, indent=2),
    )
