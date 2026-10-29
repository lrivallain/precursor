"""Persisted, provider-scoped conversation model preferences."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from precursor.backend.schemas.model_fallback import ModelCategoryId


class ConversationLLMSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Empty for a preset category: each preset names its own provider.
    provider: str = Field(default="", max_length=64)
    model: str = Field(default="", max_length=200)
    category: ModelCategoryId | None = None
    reasoning_effort: Literal["", "minimal", "low", "medium", "high", "xhigh", "max"] = ""
    context_tokens: int = Field(default=128_000, ge=1000, le=5_000_000)

    @field_validator("provider")
    @classmethod
    def known_provider(cls, value: str) -> str:
        from precursor.backend.services.llm.registry import PROVIDERS

        if value and value not in PROVIDERS:
            raise ValueError("Unknown LLM provider")
        return value

    @field_validator("model")
    @classmethod
    def strip_model(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def one_model_selection(self) -> ConversationLLMSelection:
        if bool(self.model) == (self.category is not None):
            raise ValueError("Choose either a model or a preset category")
        if self.model and not self.provider:
            raise ValueError("A model needs its provider")
        return self


class ConversationLLMFields(BaseModel):
    llm_selection: ConversationLLMSelection | None = None

    @field_validator("llm_selection", mode="before")
    @classmethod
    def read_saved_selection(cls, value: object) -> object:
        if isinstance(value, str):
            return ConversationLLMSelection.model_validate_json(value)
        return value
