"""User-defined model preset categories that may mix providers."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# Provider id for presets served by the Copilot SDK agents runtime.
AGENTS_PROVIDER = "agents"

ModelCategoryId = Annotated[
    str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$", max_length=40)
]


class ModelPreset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=200)
    reasoning_effort: Literal["", "minimal", "low", "medium", "high", "xhigh", "max"] = ""
    context_tokens: int = Field(default=128_000, ge=1000, le=5_000_000)
    context_tier: Literal["default", "long_context"] = "default"

    @field_validator("provider")
    @classmethod
    def known_provider(cls, value: str) -> str:
        from precursor.backend.services.llm.registry import PROVIDERS

        if value != AGENTS_PROVIDER and value not in PROVIDERS:
            raise ValueError("A preset provider must be an LLM provider id or agents")
        return value

    @field_validator("model")
    @classmethod
    def explicit_model(cls, value: str) -> str:
        value = value.strip()
        if not value or value == "auto":
            raise ValueError("A fallback preset needs an explicit model id, not auto")
        return value


class ModelCategoryDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ModelCategoryId
    label: str = Field(min_length=1, max_length=40)
    presets: list[ModelPreset] = Field(default_factory=list, max_length=12)

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A category needs a name")
        return value


class ModelCategories(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: list[ModelCategoryDef] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def distinct(self) -> ModelCategories:
        ids = [c.id for c in self.categories]
        if len(ids) != len(set(ids)):
            raise ValueError("Category ids must be unique")
        seen: set[tuple[str, str, str, str | int]] = set()
        for category in self.categories:
            for p in category.presets:
                signature = (
                    p.provider,
                    p.model,
                    p.reasoning_effort,
                    p.context_tier if p.provider == AGENTS_PROVIDER else p.context_tokens,
                )
                if signature in seen:
                    raise ValueError("Each provider/model/context/effort preset must appear once")
                seen.add(signature)
        return self

    def get(self, category_id: str) -> ModelCategoryDef | None:
        return next((c for c in self.categories if c.id == category_id), None)


_LEGACY_LABELS = {
    "efficiency": "Efficiency",
    "balanced": "Balanced",
    "intelligence": "Intelligence",
}


def normalize_stored_categories(raw: object) -> ModelCategories:
    """Accept the current shape or the legacy ``{scope: {tier: [presets]}}`` one."""
    if not isinstance(raw, dict) or not raw:
        return ModelCategories()
    if "categories" in raw:
        return ModelCategories.model_validate(raw)
    merged: dict[str, list[dict[str, object]]] = {name: [] for name in _LEGACY_LABELS}
    for scope, tiers in raw.items():
        if not isinstance(tiers, dict):
            continue
        for name in _LEGACY_LABELS:
            for preset in tiers.get(name) or []:
                if isinstance(preset, dict):
                    merged[name].append({**preset, "provider": scope})
    return ModelCategories.model_validate(
        {
            "categories": [
                {"id": name, "label": _LEGACY_LABELS[name], "presets": presets}
                for name, presets in merged.items()
                if presets
            ]
        }
    )
