from __future__ import annotations

from pydantic import BaseModel


class LLMModelRead(BaseModel):
    id: str
    name: str
    publisher: str = ""
    summary: str = ""
    tags: list[str] = []
    context_window: int | None = None


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
