"""LLM provider protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(slots=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    # For assistant turns that issued tool calls.
    tool_calls: list[dict[str, Any]] | None = None
    # For tool turns: identifier of the call this result responds to.
    tool_call_id: str | None = None
    # For tool turns: name of the called tool (some providers want it echoed).
    name: str | None = None
    # For user turns: image data URLs to deliver alongside the text. Providers
    # that understand vision content-parts translate these; others ignore them.
    image_urls: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LLMModel:
    id: str
    name: str
    publisher: str = ""
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    # Maximum input tokens the model accepts. ``None`` when unknown
    # (provider didn't advertise it).
    context_window: int | None = None


@dataclass(slots=True)
class ToolDef:
    """Tool advertised to the model. ``parameters`` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(slots=True)
class ToolCallRequest:
    """Model-issued tool call (assembled from streamed deltas)."""

    id: str
    name: str
    arguments: str  # raw JSON string as emitted by the model


@dataclass(slots=True)
class TextDeltaEvent:
    kind: Literal["text"] = "text"
    content: str = ""


@dataclass(slots=True)
class ToolCallsEvent:
    """All tool calls accumulated for a single assistant turn."""

    kind: Literal["tool_calls"] = "tool_calls"
    calls: list[ToolCallRequest] = field(default_factory=list)


@dataclass(slots=True)
class TurnDoneEvent:
    """Marks the end of one provider invocation (one round-trip)."""

    kind: Literal["turn_done"] = "turn_done"
    finish_reason: str | None = None


@dataclass(slots=True)
class UsageEvent:
    """Token-usage report from a provider for one round-trip."""

    kind: Literal["usage"] = "usage"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


ProviderEvent = TextDeltaEvent | ToolCallsEvent | TurnDoneEvent | UsageEvent


class LLMProvider(Protocol):
    name: str

    # Declared as plain (non-async) methods returning AsyncIterator: the
    # implementations are async generators, so *calling* them yields an async
    # iterator directly (consumed via ``async for``), not a coroutine to await.
    def stream_chat(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
    ) -> AsyncIterator[str]:
        """Yield text deltas for a tool-less assistant reply."""
        ...

    def stream_chat_with_tools(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDef],
    ) -> AsyncIterator[ProviderEvent]:
        """Yield typed events for a tool-capable assistant reply.

        The chat router consumes this in a loop: collect text + tool calls,
        execute the calls, append ``tool`` messages, call again until the
        model returns no further tool calls.
        """
        ...

    async def list_models(self) -> list[LLMModel]:
        """Return the catalog of selectable models for this provider."""
        ...
