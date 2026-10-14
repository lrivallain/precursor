"""Retry rejected requests using explicit same-category presets."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from precursor.backend.schemas.model_fallback import ModelCategories, ModelPreset
from precursor.backend.services.llm.base import (
    ChatMessage,
    LLMError,
    LLMModel,
    LLMProvider,
    ProviderEvent,
    TextDeltaEvent,
    ToolDef,
)
from precursor.backend.services.model_fallbacks import (
    ModelSelection,
    is_model_rejection,
    matching_category,
    scoped_categories,
    selected_category_presets,
)

logger = logging.getLogger(__name__)


class CategoryFallbackProvider:
    def __init__(
        self,
        provider: LLMProvider,
        categories: ModelCategories,
        context_tokens: int,
        *,
        provider_id: str = "",
        selected_category: str | None = None,
        providers: Mapping[str, LLMProvider | LLMError] | None = None,
    ) -> None:
        self._provider = provider
        self._categories = categories
        self._context_tokens = context_tokens
        self._provider_id = provider_id or provider.name
        self._selected_category = selected_category
        # Every provider a preset may name, built up front: the request-scoped
        # session that read the credentials is gone once the stream runs.
        self._providers: dict[str, LLMProvider | LLMError] = (
            dict(providers) if providers is not None else {self._provider_id: provider}
        )
        self._chosen: dict[tuple[str, str], ModelPreset | ModelSelection] = {}
        self._catalogs: dict[str, list[LLMModel] | None] = {}
        self.name = provider.name
        self.effective_model: str | None = None
        self.effective_provider: str | None = None

    def __getattr__(self, name: str) -> Any:
        # Optional provider capabilities (notably embeddings) stay untouched.
        return getattr(self._provider, name)

    async def list_models(self) -> list[LLMModel]:
        return await self._provider.list_models()

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
            if isinstance(event, TextDeltaEvent):
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
        from precursor.backend.services.context_budget import trim_messages

        selection = ModelSelection(
            model=model,
            provider=self._provider_id,
            reasoning_effort=reasoning_effort or "",
            context_tokens=self._context_tokens,
        )
        if self._selected_category is not None:
            presets = selected_category_presets(self._categories, self._selected_category)
            if not presets:
                raise LLMError(
                    f"No presets are configured for the selected category "
                    f"{self._selected_category!r}. Update Settings > Presets."
                )
            key = (f"category:{self._selected_category}", "")
            initial = self._chosen.get(key, presets[0])
        else:
            matched = matching_category(
                scoped_categories(self._categories, self._provider_id), selection
            )
            full = self._categories.get(matched.id) if matched is not None else None
            presets = list(full.presets) if full is not None else []
            key = (model, reasoning_effort or "")
            initial = self._chosen.get(key, selection)

        def signature(p: ModelSelection | ModelPreset) -> tuple[str, str, str, int]:
            return p.provider, p.model, p.reasoning_effort, p.context_tokens

        candidates: list[ModelSelection | ModelPreset] = [initial]
        seen = {signature(initial)}
        for p in presets:
            if signature(p) not in seen:
                seen.add(signature(p))
                candidates.append(p)
        last: BaseException | None = None
        for preset in candidates:
            target = self._providers.get(preset.provider or self._provider_id)
            if target is None or isinstance(target, LLMError):
                last = target or LLMError(f"Provider {preset.provider!r} is not configured")
                if not presets:
                    raise last
                continue
            catalog = await self._catalog_for(preset.provider or self._provider_id, target, presets)
            if presets and catalog and preset.model not in {m.id for m in catalog}:
                last = LLMError(
                    f"Model {preset.model!r} is no longer offered by {preset.provider or self.name}"
                )
                continue
            replacement = self._selected_category is not None or signature(preset) != signature(
                selection
            )
            advertised = next((m for m in catalog or [] if m.id == preset.model), None)
            context_tokens = min(
                preset.context_tokens,
                advertised.context_window
                if advertised is not None and advertised.context_window
                else preset.context_tokens,
            )
            if replacement:
                log = logger.info if self._selected_category is not None else logger.warning
                log(
                    "%s: replacing %s with same-category preset %s (effort=%s, context=%s)",
                    self.name,
                    model,
                    preset.model,
                    preset.reasoning_effort or "auto",
                    preset.context_tokens,
                )
            self.effective_model = preset.model
            self.effective_provider = preset.provider or self._provider_id
            yielded = False
            try:
                async for event in target.stream_chat_with_tools(
                    model=preset.model,
                    messages=(
                        trim_messages(
                            list(messages),
                            max_input_tokens=context_tokens,
                            per_message_max_tokens=context_tokens,
                        )
                        if replacement
                        else messages
                    ),
                    tools=tools,
                    reasoning_effort=preset.reasoning_effort or None,
                    request_options=request_options,
                ):
                    yielded = True
                    yield event
                self._chosen[key] = preset
                return
            except Exception as exc:
                if yielded or not presets or not is_model_rejection(exc):
                    raise
                logger.warning("%s: preset %s rejected: %s", self.name, preset.model, exc)
                last = exc
        label = self._selected_category if self._selected_category is not None else model
        raise LLMError(
            f"No working model preset remains in this category for {label!r}. "
            f"Update Settings > Presets. Last problem: {last}"
        ) from last

    async def _catalog_for(
        self, provider_id: str, target: LLMProvider, presets: Sequence[ModelPreset]
    ) -> list[LLMModel] | None:
        if not presets:
            return None
        if provider_id not in self._catalogs:
            try:
                self._catalogs[provider_id] = await target.list_models()
            except Exception as exc:
                logger.warning("Fallback catalogue unavailable for %s: %s", provider_id, exc)
                self._catalogs[provider_id] = None
        return self._catalogs[provider_id]
