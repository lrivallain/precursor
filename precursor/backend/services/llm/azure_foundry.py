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

A deployment whose name says Claude is tried on Claude's native Messages API
first (``https://<resource host>/anthropic/``), the only one of the three that
supports prompt caching; the OpenAI-compatible APIs remain its fallbacks.

Authentication is an API key when one is configured; otherwise the provider
signs in through the Azure CLI (``az login``), optionally against a specific
tenant, and hands the SDK a refreshable Entra ID token.
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Literal
from urllib.parse import urlparse

from anthropic import AsyncAnthropic, AsyncAnthropicFoundry
from openai import AsyncAzureOpenAI, AsyncOpenAI

from precursor.backend.services import azure_cli
from precursor.backend.services.llm._anthropic_messages import stream_anthropic_tools
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
TOKEN_RESOURCE = azure_cli.COGNITIVE_SERVICES_RESOURCE
_V1_ENDPOINT_RE = re.compile(r"/openai/v1/?$", re.IGNORECASE)


Surface = Literal["chat", "responses", "messages"]
_SURFACES: dict[Surface, tuple[Surface, ...]] = {
    "chat": ("chat", "responses"),
    "responses": ("responses", "chat"),
}
# Efforts Claude's Messages API accepts, advertised so the effort picker shows.
CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]

# endpoint -> model -> the API surface that last worked. Module-level because the
# registry builds a fresh provider per request, so instance state wouldn't
# survive to the next turn.
_SURFACE_MEMORY: dict[str, dict[str, Surface]] = {}


# Environment fallbacks of ``AsyncAnthropicFoundry`` (set by e.g. a Claude Code
# install on Foundry). Precursor's own settings are authoritative, and a stray
# ``ANTHROPIC_FOUNDRY_RESOURCE`` next to our ``base_url`` makes the SDK raise.
_FOUNDRY_ENV = (
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "ANTHROPIC_FOUNDRY_BASE_URL",
)


def foundry_messages_client(
    base_url: str, *, api_key: str = "", token_provider: Any = None, **options: Any
) -> AsyncAnthropic:
    """An ``AsyncAnthropicFoundry`` built from these arguments only.

    ``options`` are passed through to the client (``http_client``,
    ``max_retries``…).
    """
    saved = {name: os.environ.pop(name) for name in _FOUNDRY_ENV if name in os.environ}
    try:
        if api_key:
            return AsyncAnthropicFoundry(base_url=base_url, api_key=api_key, **options)
        return AsyncAnthropicFoundry(
            base_url=base_url, azure_ad_token_provider=token_provider, **options
        )
    finally:
        os.environ.update(saved)


def is_claude_deployment(name: str) -> bool:
    """Whether a deployment name looks like a Claude model."""
    return "claude" in name.lower()


class AzureCliError(LLMError, azure_cli.AzureCliError):
    """The Azure CLI could not supply a token (shown to the user as is)."""


class AzureCliTokenProvider(azure_cli.AzureCliTokenProvider):
    error = AzureCliError


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
        self._surface = _SURFACE_MEMORY.setdefault(endpoint, {})
        host = urlparse(endpoint).netloc
        # Claude's native API lives at the resource root, whichever Azure host
        # name (``*.openai.azure.com``, ``*.services.ai.azure.com``) is configured.
        self._messages_client: AsyncAnthropic | None = None
        if host:
            self._messages_client = foundry_messages_client(
                f"https://{host}/anthropic/", api_key=api_key, token_provider=credential
            )

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
        first_error: LLMError | None = None
        for surface in self._surface_order(model):
            yielded = False
            try:
                async for event in self._run_surface(
                    surface,
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
                status = getattr(exc.__cause__, "status_code", None)
                if (
                    yielded
                    or isinstance(exc, AzureCliError)
                    or "credentials" in str(exc)
                    or (isinstance(status, int) and status not in (400, 404))
                ):
                    raise
                first_error = first_error or exc
                continue
            self._surface[model] = surface
            return
        assert first_error is not None  # every surface ran and failed
        raise first_error

    def _surface_order(self, model: str) -> tuple[Surface, ...]:
        """API surfaces to try for ``model``: the remembered one, then the rest."""
        order: tuple[Surface, ...] = _SURFACES[self._default_surface]
        if self._messages_client is not None and is_claude_deployment(model):
            order = ("messages", *order)
        remembered = self._surface.get(model)
        if remembered in order:
            order = (remembered, *(s for s in order if s != remembered))
        return order

    def _run_surface(
        self,
        surface: Surface,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDef],
        reasoning_effort: str | None,
        request_options: Mapping[str, Any] | None,
    ) -> AsyncIterator[ProviderEvent]:
        common: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "reasoning_effort": reasoning_effort,
            "request_options": request_options,
        }
        if surface == "messages":
            assert self._messages_client is not None
            return stream_anthropic_tools(client=self._messages_client, **common)
        if surface == "responses":
            return stream_responses_tools(client=self._responses_client, **common)
        return stream_openai_tools(client=self._client, **common)

    async def embed(
        self, texts: Sequence[str], *, model: str, dimensions: int | None = None
    ) -> EmbeddingResult:
        return await embed_openai(self._client, texts, model=model, dimensions=dimensions)

    async def list_models(self) -> list[LLMModel]:
        # This is a configured list, not a remotely verified deployment catalog.
        return [
            LLMModel(
                id=name,
                name=name,
                publisher="Azure",
                supported_reasoning_efforts=list(CLAUDE_EFFORTS)
                if is_claude_deployment(name)
                else [],
            )
            for name in self._deployments
        ]
