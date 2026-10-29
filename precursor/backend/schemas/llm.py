from __future__ import annotations

from pydantic import BaseModel


class LLMModelRead(BaseModel):
    id: str
    name: str
    publisher: str = ""
    summary: str = ""
    tags: list[str] = []
    context_window: int | None = None
    supported_reasoning_efforts: list[str] = []
    max_output_tokens: int | None = None
    vision: bool = False
    # The actual provider, including the offline mock when config is unusable.
    catalog_provider: str | None = None


class ProviderFieldRead(BaseModel):
    name: str
    label: str
    secret: bool = False
    required: bool = False
    placeholder: str = ""
    help: str = ""


class ProviderRead(BaseModel):
    id: str
    label: str
    fields: list[ProviderFieldRead] = []
    uses_github_token: bool = False
    discovers_models: bool = True
    # Whether the OpenAI-compatible endpoint can relay to this provider.
    openai_proxy: bool = False
    # Non-empty => upstream is gone; the text explains what to use instead.
