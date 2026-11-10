"""Entra ID tokens from the Azure CLI sign-in (``az login``).

Shared by the Azure services that can run without a key (AI Foundry models,
Azure AI Speech): ``az account get-access-token`` mints a bearer token for a
resource, in the CLI's default tenant unless one is given.
"""

from __future__ import annotations

import asyncio
import json
import re
import time

COGNITIVE_SERVICES_RESOURCE = "https://cognitiveservices.azure.com"
ARM_RESOURCE = "https://management.azure.com"
_TOKEN_TIMEOUT_SECONDS = 30.0
_REFRESH_MARGIN_SECONDS = 300.0
_FALLBACK_TTL_SECONDS = 300.0
_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class AzureCliError(Exception):
    """The Azure CLI could not supply a token; the message is user-facing."""


def check_tenant(tenant: str) -> str:
    """Return ``tenant`` stripped, or raise ``ValueError`` if it isn't a plain id/domain."""
    tenant = tenant.strip()
    if tenant and not _TENANT_RE.match(tenant):
        raise ValueError("Invalid Azure tenant.")
    return tenant


class AzureCliTokenProvider:
    """Async bearer-token source backed by ``az account get-access-token``.

    An empty ``tenant`` lets the CLI use its default tenant. Tokens are cached
    until shortly before they expire so concurrent requests share one CLI call.
    Failures raise ``error``, which a caller can narrow to its own error type.
    """

    error: type[AzureCliError] = AzureCliError

    def __init__(self, tenant: str = "", resource: str = COGNITIVE_SERVICES_RESOURCE) -> None:
        self._tenant = check_tenant(tenant)
        self._resource = resource
        self._token = ""
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def tenant(self) -> str:
        return self._tenant

    async def __call__(self) -> str:
        async with self._lock:
            if self._token and time.time() < self._expires_at - _REFRESH_MARGIN_SECONDS:
                return self._token
            self._token, self._expires_at = await self._fetch()
            return self._token

    async def _fetch(self) -> tuple[str, float]:
        args = ["account", "get-access-token", "--resource", self._resource, "--output", "json"]
        if self._tenant:
            args += ["--tenant", self._tenant]
        try:
            proc = await asyncio.create_subprocess_exec(
                "az",
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise self.error(
                "Azure CLI not found. Install it and run `az login`, or set an API key."
            ) from None
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), _TOKEN_TIMEOUT_SECONDS)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise self.error("Azure CLI timed out while fetching an access token.") from None
        if proc.returncode != 0:
            detail = stderr.decode(errors="replace").strip().splitlines()
            hint = f" ({detail[-1]})" if detail else ""
            where = f" for tenant {self._tenant}" if self._tenant else ""
            raise self.error(f"Azure CLI could not get a token{where}; run `az login`.{hint}")
        try:
            payload = json.loads(stdout)
            token = str(payload["accessToken"])
        except (ValueError, KeyError, TypeError):
            raise self.error("Azure CLI returned an unreadable access token response.") from None
        expires = payload.get("expires_on")
        try:
            expires_at = float(expires)
        except (TypeError, ValueError):
            expires_at = time.time() + _FALLBACK_TTL_SECONDS + _REFRESH_MARGIN_SECONDS
        return token, expires_at
