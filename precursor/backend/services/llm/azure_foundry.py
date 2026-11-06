"""Azure AI Foundry / Azure OpenAI provider.

Targets an Azure OpenAI / AI Foundry chat deployment via the OpenAI SDK's
``AsyncAzureOpenAI`` client, which speaks the Azure URL scheme
(``{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=…``)
and the ``api-key`` header. The model id passed to ``stream_chat`` is the Azure
**deployment name**.

Azure doesn't expose a uniform per-key model catalog here, so ``list_models``
returns the configured deployment names (comma-separated, if multiple) and the
UI falls back to manual deployment-name entry.

An endpoint ending in ``/openai/v1`` (the Foundry "v1" API) is called with the
plain ``AsyncOpenAI`` client instead, since the Azure client would append its
own ``/openai/deployments/…`` path to it and 404.

Chat-completions and the Responses API are both tried whatever the endpoint
style: some deployments (Claude, for one) answer only the Responses API. A v1
endpoint tries Responses first and a classic one chat-completions first; if the
first is refused the other is tried, and the one that works is remembered.

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
from typing import Any, Literal

from openai import APIStatusError, AsyncAzureOpenAI, AsyncOpenAI

from precursor.backend.services.llm._openai_compat import (
    embed_openai,
    stream_openai_tools,
)
from precursor.backend.services.llm._responses_compat import stream_responses_tools
from precursor.backend.services.llm.base import (
    ChatMessage,
    EmbeddingResult,
    LLMError,
    LLMModel,
    ProviderEvent,
    TextDeltaEvent,
    ToolDef,
)

DEFAULT_API_VERSION = "2024-10-21"
RESPONSES_API_VERSION = "2025-04-01-preview"
TOKEN_RESOURCE = "https://cognitiveservices.azure.com"
_TOKEN_TIMEOUT_SECONDS = 30.0
_REFRESH_MARGIN_SECONDS = 300.0
_FALLBACK_TTL_SECONDS = 300.0
_V1_ENDPOINT_RE = re.compile(r"/openai/v1/?$", re.IGNORECASE)
_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


Surface = Literal["chat", "responses"]
_SURFACES: dict[Surface, tuple[Surface, Surface]] = {
    "chat": ("chat", "responses"),
    "responses": ("responses", "chat"),
}


class AzureCliError(LLMError):
    """The Azure CLI could not supply a token."""


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
            raise AzureCliError(
                "Azure CLI not found. Install it and run `az login`, or set an API key."
            ) from None
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), _TOKEN_TIMEOUT_SECONDS)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise AzureCliError("Azure CLI timed out while fetching an access token.") from None
        if proc.returncode != 0:
            detail = stderr.decode(errors="replace").strip().splitlines()
            hint = f" ({detail[-1]})" if detail else ""
            where = f" for tenant {self._tenant}" if self._tenant else ""
            raise AzureCliError(f"Azure CLI could not get a token{where}; run `az login`.{hint}")
        try:
            payload = json.loads(stdout)
            token = str(payload["accessToken"])
        except (ValueError, KeyError, TypeError):
            raise AzureCliError("Azure CLI returned an unreadable access token response.") from None
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
        api_version: str = "",
        deployment: str = "",
        tenant: str = "",
    ) -> None:
        self._deployments = list(
            dict.fromkeys(
                name.strip() for name in deployment.replace("\n", ",").split(",") if name.strip()
            )
        )
        self._deployment = self._deployments[0] if self._deployments else ""
        endpoint = endpoint.strip().rstrip("/")
        credential: Any = api_key or AzureCliTokenProvider(tenant)
        v1 = bool(_V1_ENDPOINT_RE.search(endpoint))
        # Either API may serve a deployment whatever the URL style, so the other
        # one is the fallback; the surface that last worked is remembered per model.
        self._default_surface: Surface = "responses" if v1 else "chat"
        self._surface: dict[str, Surface] = {}

        def azure_client(version: str) -> AsyncOpenAI:
            if api_key:
                return AsyncAzureOpenAI(
                    azure_endpoint=endpoint, api_key=api_key, api_version=version
                )
            return AsyncAzureOpenAI(
                azure_endpoint=endpoint, azure_ad_token_provider=credential, api_version=version
            )

        self._client: AsyncOpenAI
        self._responses_client: AsyncOpenAI
        if v1:
            self._client = self._responses_client = AsyncOpenAI(
                base_url=endpoint + "/", api_key=credential
            )
        else:
            self._client = azure_client(api_version or DEFAULT_API_VERSION)
            # The default chat version predates the Responses API; an explicitly
            # configured version is the user's call and is used for both.
            self._responses_client = (
                azure_client(api_version) if api_version else azure_client(RESPONSES_API_VERSION)
            )

    async def stream_chat(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[str]:
        async for event in self.stream_chat_with_tools(
            model=model, messages=messages, tools=(), reasoning_effort=reasoning_effort
        ):
            if isinstance(event, TextDeltaEvent) and event.content:
                yield event.content

    async def stream_chat_with_tools(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDef],
        reasoning_effort: str | None = None,
        request_options: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        model = model or self._deployment
        surfaces = _SURFACES[self._surface.get(model, self._default_surface)]
        first_error: LLMError | None = None
        for surface in surfaces:
            run = stream_responses_tools if surface == "responses" else stream_openai_tools
            client = self._responses_client if surface == "responses" else self._client
            yielded = False
            try:
                async for event in run(
                    client=client,
                    model=model,
                    messages=messages,
                    tools=tools,
                    reasoning_effort=reasoning_effort,
                    request_options=request_options,
                ):
                    yielded = True
                    yield event
            except LLMError as exc:
                # The SDK already retries transient HTTP failures. Changing API
                # surfaces would multiply those attempts against the same quota.
                cause = exc.__cause__
                if (
                    yielded
                    or isinstance(exc, AzureCliError)
                    or "credentials" in str(exc)
                    or (isinstance(cause, APIStatusError) and cause.status_code not in (400, 404))
                ):
                    raise
                first_error = first_error or exc
                continue
            self._surface[model] = surface
            return
        assert first_error is not None  # both surfaces ran and failed
        raise first_error

    async def embed(
        self, texts: Sequence[str], *, model: str, dimensions: int | None = None
    ) -> EmbeddingResult:
        return await embed_openai(self._client, texts, model=model, dimensions=dimensions)

    async def list_models(self) -> list[LLMModel]:
        # This is a configured list, not a remotely verified deployment catalog.
        return [LLMModel(id=name, name=name, publisher="Azure") for name in self._deployments]
