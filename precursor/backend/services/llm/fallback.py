"""Retry rejected requests using explicit same-category presets."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from precursor.backend.schemas.model_fallback import ModelCategories, ModelCategory, ModelPreset
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
    category_presets,
    is_model_rejection,
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
        selected_category: ModelCategory | None = None,
        category_provider_ready: bool = True,
    ) -> None:
        self._provider = provider
        self._categories = categories
        self._context_tokens = context_tokens
        self._selected_category = selected_category
        self._category_provider_ready = category_provider_ready
        self._chosen: dict[tuple[str, str], ModelPreset | ModelSelection] = {}
        self._catalog: list[LLMModel] | None = None
        self._catalog_loaded = False
        self.name = provider.name
        self.effective_model: str | None = None

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

        if self._selected_category is not None and not self._category_provider_ready:
            raise LLMError(
                "The selected category's model provider is not configured. "
                "Check credentials in Settings > Model."
            )
        selection = ModelSelection(
            model=model,
            reasoning_effort=reasoning_effort or "",
            context_tokens=self._context_tokens,
        )
        if self._selected_category is not None:
            presets = selected_category_presets(self._categories, self._selected_category)
            if not presets:
                raise LLMError(
                    f"No presets are configured for the selected category "
                    f"{self._selected_category.value!r}. Update Settings > Model > Manage presets."
                )
            key = (f"category:{self._selected_category.value}", "")
            initial = self._chosen.get(key, presets[0])
        else:
            presets = category_presets(self._categories, selection)
            key = (model, reasoning_effort or "")
            initial = self._chosen.get(key, selection)

        def signature(p: ModelSelection | ModelPreset) -> tuple[str, str, int]:
            return p.model, p.reasoning_effort, p.context_tokens

        candidates: list[ModelSelection | ModelPreset] = [initial]
        seen = {signature(initial)}
        for p in presets:
            if signature(p) not in seen:
                seen.add(signature(p))
                candidates.append(p)
        if presets and not self._catalog_loaded:
            self._catalog_loaded = True
            try:
                self._catalog = await self.list_models()
            except Exception as exc:
                logger.warning("Fallback catalogue unavailable for %s: %s", self.name, exc)
        offered = {m.id for m in self._catalog or []}
        last: BaseException | None = None
        for preset in candidates:
            if presets and offered and preset.model not in offered:
                last = LLMError(f"Model {preset.model!r} is no longer offered by {self.name}")
                continue
            replacement = self._selected_category is not None or signature(preset) != signature(
                selection
            )
            advertised = next((m for m in self._catalog or [] if m.id == preset.model), None)
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
            yielded = False
            try:
                async for event in self._provider.stream_chat_with_tools(
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
        target = self._selected_category.value if self._selected_category is not None else model
        raise LLMError(
            f"No working model preset remains in this category for {target!r}. "
            f"Update Settings > Model > Model alternatives. Last rejection: {last}"
        ) from last
