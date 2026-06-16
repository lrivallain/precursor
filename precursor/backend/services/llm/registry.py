"""LLM provider registry.

A single declarative catalog of the available providers: each entry describes
its human label, the config fields the user must supply (so the Settings UI can
render the right inputs and redact secrets), and how to construct the provider.

Adding a provider = add one ``ProviderSpec`` here plus its implementation class;
the factory, the settings schema, and the UI all pick it up from this registry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from precursor.backend.services.llm.azure_foundry import AzureFoundryProvider
from precursor.backend.services.llm.base import LLMProvider
from precursor.backend.services.llm.github_copilot import GitHubCopilotProvider
from precursor.backend.services.llm.github_models import GitHubModelsProvider
from precursor.backend.services.llm.mock import MockProvider
from precursor.backend.services.llm.openai_compatible import OpenAICompatibleProvider


@dataclass(slots=True, frozen=True)
class ProviderField:
    """One configurable input for a provider (rendered in Settings)."""

    name: str
    label: str
    secret: bool = False
    required: bool = False
    placeholder: str = ""
    help: str = ""


@dataclass(slots=True, frozen=True)
class ProviderSpec:
    id: str
    label: str
    # Per-provider config inputs (stored under settings ``llm_providers[id]``).
    fields: tuple[ProviderField, ...] = ()
    # When True the provider authenticates with the resolved GitHub token
    # (settings → gh CLI) rather than its own config.
    uses_github_token: bool = False
    # Builds the provider from its resolved config dict and the GitHub token.
    build: Callable[[dict[str, str], str], LLMProvider] = field(
        default=lambda _cfg, _tok: MockProvider()
    )
    # Hint for the UI: whether ``list_models`` is expected to return a catalog.
    discovers_models: bool = True


def _build_azure(cfg: dict[str, str], _token: str) -> LLMProvider:
    return AzureFoundryProvider(
        endpoint=cfg.get("endpoint", ""),
        api_key=cfg.get("key", ""),
        api_version=cfg.get("api_version", ""),
        deployment=cfg.get("deployment", ""),
    )


def _build_openai_compatible(
    name: str, default_base_url: str, publisher: str = ""
) -> Callable[[dict[str, str], str], LLMProvider]:
    def builder(cfg: dict[str, str], _token: str) -> LLMProvider:
        return OpenAICompatibleProvider(
            name=name,
            base_url=cfg.get("base_url") or default_base_url,
            api_key=cfg.get("key", ""),
            publisher=publisher,
        )

    return builder


_KEY_FIELD = ProviderField(
    name="key", label="API key", secret=True, required=True, placeholder="sk-…"
)
_BASE_URL_OPTIONAL = ProviderField(
    name="base_url",
    label="Base URL (optional)",
    placeholder="override the default endpoint",
)

PROVIDERS: dict[str, ProviderSpec] = {
    "github_copilot": ProviderSpec(
        id="github_copilot",
        label="GitHub Copilot",
        uses_github_token=True,
        build=lambda _cfg, token: GitHubCopilotProvider(token=token),
    ),
    "github_models": ProviderSpec(
        id="github_models",
        label="GitHub Models",
        uses_github_token=True,
        build=lambda _cfg, token: GitHubModelsProvider(token=token),
    ),
    "azure_foundry": ProviderSpec(
        id="azure_foundry",
        label="Azure AI Foundry",
        fields=(
            ProviderField(
                name="endpoint",
                label="Endpoint",
                required=True,
                placeholder="https://<resource>.openai.azure.com",
            ),
            ProviderField(
                name="deployment",
                label="Deployment name",
                placeholder="my-gpt-4o (used as the model id)",
                help="Used as the model id; also fill the Model field with it.",
            ),
            ProviderField(
                name="api_version",
                label="API version (optional)",
                placeholder="2024-10-21",
            ),
            ProviderField(name="key", label="API key", secret=True, required=True),
        ),
        build=_build_azure,
        discovers_models=False,
    ),
    "openai": ProviderSpec(
        id="openai",
        label="OpenAI",
        fields=(_KEY_FIELD, _BASE_URL_OPTIONAL),
        build=_build_openai_compatible("openai", "https://api.openai.com/v1", "OpenAI"),
    ),
    "mistral": ProviderSpec(
        id="mistral",
        label="Mistral AI",
        fields=(_KEY_FIELD, _BASE_URL_OPTIONAL),
        build=_build_openai_compatible("mistral", "https://api.mistral.ai/v1", "Mistral"),
    ),
    "huggingface": ProviderSpec(
        id="huggingface",
        label="Hugging Face",
        fields=(
            ProviderField(
                name="key", label="Access token", secret=True, required=True, placeholder="hf_…"
            ),
            _BASE_URL_OPTIONAL,
        ),
        build=_build_openai_compatible(
            "huggingface", "https://router.huggingface.co/v1", "Hugging Face"
        ),
    ),
    "ollama": ProviderSpec(
        id="ollama",
        label="Ollama (local)",
        fields=(
            ProviderField(
                name="base_url",
                label="Base URL",
                placeholder="http://localhost:11434/v1",
            ),
        ),
        build=_build_openai_compatible("ollama", "http://localhost:11434/v1", "Ollama"),
    ),
    "mock": ProviderSpec(
        id="mock",
        label="Mock (offline)",
        build=lambda _cfg, _token: MockProvider(),
        discovers_models=True,
    ),
}

DEFAULT_PROVIDER = "github_copilot"


def provider_secret_fields(provider_id: str) -> set[str]:
    spec = PROVIDERS.get(provider_id)
    if spec is None:
        return set()
    return {f.name for f in spec.fields if f.secret}
