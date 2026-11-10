"""Azure AI Speech token broker.

The browser talks to Azure Speech directly (via the Speech JS SDK), but must
never hold the subscription key. This mints a **short-lived authorization
token** for the SPA, so the secret never leaves the backend — the standard
browser-app pattern. Two credentials are supported:

* the subscription key, exchanged at the resource's STS ``issueToken`` endpoint
  for a ~10-minute token;
* the Azure CLI sign-in (``az login``): an Entra ID token for Cognitive
  Services, handed over as ``aad#<resource id>#<token>``, the form the Speech
  SDK expects. Entra ID only works on a resource with a custom domain
  (``https://<name>.cognitiveservices.azure.com``), and the signed-in account
  needs the *Cognitive Services Speech User* role on it. When the resource id
  isn't configured it is looked up with Azure Resource Graph from the
  endpoint's custom subdomain.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from precursor.backend.services.azure_cli import (
    ARM_RESOURCE,
    COGNITIVE_SERVICES_RESOURCE,
    AzureCliError,
    AzureCliTokenProvider,
    check_tenant,
)
from precursor.backend.services.speech_settings import SpeechConfig

_RESOURCE_GRAPH_URL = (
    "https://management.azure.com/providers/Microsoft.ResourceGraph/resources"
    "?api-version=2022-10-01"
)
_REGIONAL_HOST_SUFFIX = ".api.cognitive.microsoft.com"
_SUBDOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_RESOURCE_ID_RE = re.compile(
    r"^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/"
    r"Microsoft\.CognitiveServices/accounts/[^/]+$",
    re.IGNORECASE,
)

# Token providers and discovered resource ids, kept across requests so the
# browser's token refreshes don't each spawn the CLI. Keyed by tenant (and host).
_TOKEN_PROVIDERS: dict[tuple[str, str], AzureCliTokenProvider] = {}
_RESOURCE_IDS: dict[tuple[str, str], str] = {}


class SpeechAuthError(AzureCliError):
    """Entra ID sign-in to Speech is not possible as configured (user-facing)."""


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


def custom_subdomain(endpoint: str) -> str:
    """The resource's custom subdomain, from its endpoint URL.

    Raises ``SpeechAuthError`` for a regional endpoint, which Entra ID can't use.
    """
    host = (urlparse(endpoint.strip()).hostname or "").lower()
    if not host:
        raise SpeechAuthError("Set the Speech endpoint URL.")
    if host.endswith(_REGIONAL_HOST_SUFFIX):
        raise SpeechAuthError(
            "Azure CLI sign-in needs the resource's custom-domain endpoint "
            "(https://<name>.cognitiveservices.azure.com), not a regional one."
        )
    name = host.split(".", 1)[0]
    if not _SUBDOMAIN_RE.match(name):
        raise SpeechAuthError(f"Unexpected Speech endpoint host: {host}.")
    return name


def _token_provider(tenant: str, resource: str) -> AzureCliTokenProvider:
    key = (tenant, resource)
    if key not in _TOKEN_PROVIDERS:
        _TOKEN_PROVIDERS[key] = AzureCliTokenProvider(tenant, resource)
    return _TOKEN_PROVIDERS[key]


async def discover_resource_id(subdomain: str, tenant: str) -> str:
    """Find the Speech resource's ARM id from its custom subdomain."""
    cache_key = (tenant, subdomain)
    if cache_key in _RESOURCE_IDS:
        return _RESOURCE_IDS[cache_key]
    arm_token = await _token_provider(tenant, ARM_RESOURCE)()
    query = (
        "Resources | where type =~ 'microsoft.cognitiveservices/accounts'"
        f" | where properties.customSubDomainName =~ '{subdomain}' | project id"
    )
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            _RESOURCE_GRAPH_URL,
            headers={"Authorization": f"Bearer {arm_token}"},
            json={"query": query},
        )
    if resp.status_code >= 400:
        raise SpeechAuthError(
            f"Could not look up the Speech resource in Azure ({resp.status_code}); "
            "set its resource ID in Settings."
        )
    rows = resp.json().get("data") or []
    ids: list[str] = [
        row["id"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)
    ]
    if len(ids) != 1:
        where = f" in tenant {tenant}" if tenant else " in the CLI's default tenant"
        raise SpeechAuthError(
            f"Found no Speech resource named '{subdomain}'{where}; "
            "check the tenant or set the resource ID in Settings."
        )
    _RESOURCE_IDS[cache_key] = ids[0]
    return ids[0]


async def mint_entra_speech_token(endpoint: str, *, tenant: str = "", resource_id: str = "") -> str:
    """An Entra ID Speech token from the Azure CLI sign-in, in the SDK's ``aad#`` form."""
    try:
        tenant = check_tenant(tenant)
    except ValueError:
        raise SpeechAuthError("Invalid Azure tenant.") from None
    subdomain = custom_subdomain(endpoint)
    resource_id = resource_id.strip()
    if resource_id and not _RESOURCE_ID_RE.match(resource_id):
        raise SpeechAuthError(
            "The resource ID should look like /subscriptions/…/resourceGroups/…/"
            "providers/Microsoft.CognitiveServices/accounts/<name>."
        )
    resource_id = resource_id or await discover_resource_id(subdomain, tenant)
    token = await _token_provider(tenant, COGNITIVE_SERVICES_RESOURCE)()
    return f"aad#{resource_id}#{token}"


async def mint_token_for(config: SpeechConfig) -> str:
    """A browser token for ``config``'s credential.

    Raises ``httpx.HTTPError`` (key) or ``AzureCliError`` (Azure CLI sign-in).
    """
    if config.auth == "azure_cli":
        return await mint_entra_speech_token(
            config.endpoint, tenant=config.tenant, resource_id=config.resource_id
        )
    return await mint_speech_token(config.key, config.endpoint)
