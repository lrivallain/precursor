"""Azure AI Foundry / Azure OpenAI provider.

Targets an Azure OpenAI / AI Foundry chat deployment via the OpenAI SDK's
``AsyncAzureOpenAI`` client, which speaks the Azure URL scheme
(``{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=…``)
and the ``api-key`` header. The model id passed to ``stream_chat`` is the Azure
**deployment name**.

Azure doesn't expose a uniform per-key model catalog here, so ``list_models``
returns the configured deployment names (comma-separated, if multiple) and the
UI falls back to manual deployment-name entry.

Authentication is an API key when one is configured; otherwise the provider
signs in through the Azure CLI (``az login``), optionally against a specific
tenant, and hands the SDK a refreshable Entra ID token.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from openai import AsyncAzureOpenAI

from precursor.backend.services.llm._openai_compat import (
    embed_openai,
    stream_openai_tools,
    to_openai_messages,
)
from precursor.backend.services.llm.base import (
    ChatMessage,
    EmbeddingResult,
    LLMError,
    LLMModel,
    ProviderEvent,
    ToolDef,
)

DEFAULT_API_VERSION = "2024-10-21"
TOKEN_RESOURCE = "https://cognitiveservices.azure.com"
_TOKEN_TIMEOUT_SECONDS = 30.0
_REFRESH_MARGIN_SECONDS = 300.0
_FALLBACK_TTL_SECONDS = 300.0
_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class AzureCliTokenProvider:
    """Async bearer-token source backed by ``az account get-access-token``.

    An empty ``tenant`` lets the CLI use its default tenant. Tokens are cached
    until shortly before they expire so concurrent requests share one CLI call.
    """

    def __init__(self, tenant: str = "") -> None:
        tenant = tenant.strip()
        if tenant and not _TENANT_RE.match(tenant):
            raise ValueError("Invalid Azure tenant.")
        self._tenant = tenant
        self._token = ""
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def __call__(self) -> str:
        async with self._lock:
            if self._token and time.time() < self._expires_at - _REFRESH_MARGIN_SECONDS:
                return self._token
            self._token, self._expires_at = await self._fetch()
            return self._token

    async def _fetch(self) -> tuple[str, float]:
        args = ["account", "get-access-token", "--resource", TOKEN_RESOURCE, "--output", "json"]
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
            raise LLMError(
                "Azure CLI not found. Install it and run `az login`, or set an API key."
            ) from None
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), _TOKEN_TIMEOUT_SECONDS)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise LLMError("Azure CLI timed out while fetching an access token.") from None
        if proc.returncode != 0:
            detail = stderr.decode(errors="replace").strip().splitlines()
            hint = f" ({detail[-1]})" if detail else ""
            where = f" for tenant {self._tenant}" if self._tenant else ""
            raise LLMError(f"Azure CLI could not get a token{where}; run `az login`.{hint}")
        try:
            payload = json.loads(stdout)
            token = str(payload["accessToken"])
        except (ValueError, KeyError, TypeError):
            raise LLMError("Azure CLI returned an unreadable access token response.") from None
        expires = payload.get("expires_on")
        try:
            expires_at = float(expires)
        except (TypeError, ValueError):
            expires_at = time.time() + _FALLBACK_TTL_SECONDS + _REFRESH_MARGIN_SECONDS
        return token, expires_at


class AzureFoundryProvider:
    name = "azure_foundry"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str = "",
        api_version: str = DEFAULT_API_VERSION,
        deployment: str = "",
        tenant: str = "",
    ) -> None:
        self._deployments = list(
            dict.fromkeys(
                name.strip() for name in deployment.replace("\n", ",").split(",") if name.strip()
            )
        )
        self._deployment = self._deployments[0] if self._deployments else ""
        auth: dict[str, Any] = (
            {"api_key": api_key}
            if api_key
            else {"azure_ad_token_provider": AzureCliTokenProvider(tenant)}
        )
        self._client = AsyncAzureOpenAI(
            azure_endpoint=endpoint.rstrip("/"),
            api_version=api_version or DEFAULT_API_VERSION,
            **auth,
        )

    async def stream_chat(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[str]:
        kwargs: dict[str, Any] = {
            "model": model or self._deployment,
            "messages": to_openai_messages(messages),
            "stream": True,
        }
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    async def stream_chat_with_tools(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDef],
        reasoning_effort: str | None = None,
        request_options: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        async for event in stream_openai_tools(
            client=self._client,
            model=model or self._deployment,
            messages=messages,
            tools=tools,
            reasoning_effort=reasoning_effort,
            request_options=request_options,
        ):
            yield event

    async def embed(
        self, texts: Sequence[str], *, model: str, dimensions: int | None = None
    ) -> EmbeddingResult:
        return await embed_openai(self._client, texts, model=model, dimensions=dimensions)

    async def list_models(self) -> list[LLMModel]:
        # This is a configured list, not a remotely verified deployment catalog.
        return [LLMModel(id=name, name=name, publisher="Azure") for name in self._deployments]
