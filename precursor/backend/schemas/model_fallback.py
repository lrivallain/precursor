"""Optional, provider-scoped model replacement presets."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ModelCategory(StrEnum):
    EFFICIENCY = "efficiency"
    BALANCED = "balanced"
    INTELLIGENCE = "intelligence"


class ModelPreset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1, max_length=200)
    reasoning_effort: Literal["", "minimal", "low", "medium", "high", "xhigh", "max"] = ""
    context_tokens: int = Field(default=128_000, ge=1000, le=5_000_000)
    context_tier: Literal["default", "long_context"] = "default"

    @field_validator("model")
    @classmethod
    def explicit_model(cls, value: str) -> str:
        value = value.strip()
        if not value or value == "auto":
            raise ValueError("A fallback preset needs an explicit model id, not auto")
        return value


class ModelCategories(BaseModel):
    model_config = ConfigDict(extra="forbid")

    efficiency: list[ModelPreset] = Field(default_factory=list, max_length=12)
    balanced: list[ModelPreset] = Field(default_factory=list, max_length=12)
    intelligence: list[ModelPreset] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def distinct_presets(self) -> ModelCategories:
        seen: set[ModelPreset] = set()
        for presets in (self.efficiency, self.balanced, self.intelligence):
            for preset in presets:
                if preset in seen:
                    raise ValueError("Each model/context/effort preset must appear only once")
                seen.add(preset)
        return self
