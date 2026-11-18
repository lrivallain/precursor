"""Talking to Application Insights without its SDK.

The ingestion endpoint takes a JSON array of envelopes (``/v2/track``). Writing
them by hand keeps the dependency list as it is and, more to the point, sends
only what this module builds: the SDK's auto-instrumentation would add request
URLs, SQL and log lines, which is exactly the user data we must not ship.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from precursor.backend.clock import utcnow

logger = logging.getLogger(__name__)

_DEFAULT_ENDPOINT = "https://dc.services.visualstudio.com/"
_TIMEOUT = 10.0
# Lets the ingestion transformation tell our envelopes from anything else.
SDK_VERSION = "precursor-telemetry:1"


@dataclass(frozen=True)
class Target:
    instrumentation_key: str
    ingestion_endpoint: str

    @property
    def track_url(self) -> str:
        return self.ingestion_endpoint.rstrip("/") + "/v2/track"


def parse_connection_string(raw: str) -> Target | None:
    """The key and endpoint of an Application Insights connection string."""
    parts: dict[str, str] = {}
    for chunk in raw.split(";"):
        key, sep, value = chunk.partition("=")
        if sep and value.strip():
            parts[key.strip().lower()] = value.strip()
    ikey = parts.get("instrumentationkey")
    if not ikey:
        return None
    endpoint = parts.get("ingestionendpoint", _DEFAULT_ENDPOINT)
    if not endpoint.startswith("https://"):
        return None
    return Target(ikey, endpoint)


def _now() -> str:
    return utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def envelope(
    target: Target,
    *,
    kind: str,
    base_type: str,
    base_data: dict[str, Any],
    tags: dict[str, str],
) -> dict[str, Any]:
    """One telemetry item, as the ingestion endpoint expects it."""
    return {
        "name": f"Microsoft.ApplicationInsights.{kind}",
        "time": _now(),
        "iKey": target.instrumentation_key,
        "tags": {"ai.internal.sdkVersion": SDK_VERSION, **tags},
        "data": {"baseType": base_type, "baseData": {"ver": 2, **base_data}},
    }


async def post(target: Target, envelopes: list[dict[str, Any]]) -> bool:
    """Send ``envelopes``; True when the endpoint accepted all of them.

    Never raises: telemetry that fails is dropped, it must not get in the way.
    """
    if not envelopes:
        return True
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(target.track_url, json=envelopes)
    except httpx.HTTPError as exc:
        logger.debug("Telemetry not sent: %s", type(exc).__name__)
        return False
    if response.status_code != 200:
        logger.debug("Telemetry refused: HTTP %s", response.status_code)
        return False
    return True
