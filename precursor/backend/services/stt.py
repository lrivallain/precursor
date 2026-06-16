"""Azure AI Speech token broker.

The browser talks to Azure Speech directly (via the Speech JS SDK), but must
never hold the subscription key. This mints a **short-lived authorization
token** (valid ~10 minutes) from the key so the SPA can authenticate to Azure
without the secret ever leaving the backend — the standard browser-app pattern.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx


def _issue_token_url(endpoint: str) -> str:
    """Derive the STS issueToken URL from a Speech resource endpoint.

    Works for both custom-domain (``https://<name>.cognitiveservices.azure.com``)
    and regional (``https://<region>.api.cognitive.microsoft.com``) endpoints by
    appending the standard ``/sts/v1.0/issueToken`` path to the origin.
    """
    parsed = urlparse(endpoint)
    return f"{parsed.scheme}://{parsed.netloc}/sts/v1.0/issueToken"


async def mint_speech_token(key: str, endpoint: str) -> str:
    """Exchange an Azure Speech subscription key for a short-lived token."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            _issue_token_url(endpoint),
            headers={
                "Ocp-Apim-Subscription-Key": key,
                "Content-Length": "0",
            },
        )
    resp.raise_for_status()
    return resp.text
