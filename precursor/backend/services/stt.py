"""Azure AI Speech token broker.

The browser talks to Azure Speech directly (via the Speech JS SDK), but must
never hold the subscription key. This mints a **short-lived authorization
token** (valid ~10 minutes) from the key so the SPA can authenticate to Azure
without the secret ever leaving the backend — the standard browser-app pattern.
"""

from __future__ import annotations

import httpx

# Azure regional Security Token Service. Returns a JWT (plain text) valid ~10m.
_STS_TEMPLATE = "https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"


async def mint_speech_token(key: str, region: str) -> str:
    """Exchange an Azure Speech subscription key for a short-lived token."""
    url = _STS_TEMPLATE.format(region=region)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url,
            headers={
                "Ocp-Apim-Subscription-Key": key,
                "Content-Length": "0",
            },
        )
    resp.raise_for_status()
    return resp.text
