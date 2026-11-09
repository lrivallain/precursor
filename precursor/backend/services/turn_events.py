"""The events a turn yields, from the tool loop to its consumers.

:func:`~precursor.backend.services.turn_engine.run_tool_loop` yields these;
the SSE stream and the scheduler each turn them into their own persistence and
transport. Re-exported by ``turn_engine``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from precursor.backend.services import approvals as approval_service
from precursor.backend.services.llm.base import UsageEvent


@dataclass(slots=True)
class AssistantTextDelta:
    """A streamed chunk of assistant text."""

    content: str


@dataclass(slots=True)
class AssistantReasoningDelta:
    """A streamed chunk of the model's thinking for the current round."""

    content: str


@dataclass(slots=True)
class AssistantFinalTurn:
    """The model finished with a plain-text answer (no tool calls)."""

    text: str
    usage: UsageEvent | None
    reasoning: str = ""
    model: str | None = None
    provider: str | None = None


@dataclass(slots=True)
class AssistantToolCallsTurn:
    """The model requested one or more tool calls."""

    text: str
    tool_calls: list[Any]
    openai_tool_calls: list[dict[str, Any]]
    usage: UsageEvent | None
    reasoning: str = ""
    model: str | None = None
    provider: str | None = None


@dataclass(slots=True)
class ToolResultTurn:
    """A single tool call was executed."""

    call: Any
    result_text: str
    is_error: bool
    # ``{"slug", "path"}`` (+ ``written`` for a change) when the tool touched a
    # workspace file, so the UI can
    # offer a link to it without parsing ``result_text`` — which for a read
    # embeds the whole file. None for every other tool.
    link: dict[str, Any] | None = None
    # The approval gate refused the call and the loop answered it instead of parking.
    declined: bool = False


@dataclass(slots=True)
class ToolAuthRequired:
    """A tool the model called needs an interactive sign-in before it can run.

    Yielded at *call* time rather than turn-start time: the catalogue is
    advertised from the stored one even while a credential is stale, so an
    unrelated question is never held hostage by one expired token. The prompt is
    also strictly better than the old blanket one, because it can name the tool
    that actually needs the sign-in.
    """

    server: str
    message: str
    tool: str


@dataclass(slots=True)
class ToolCallOutcome:
    """How one tool call ended, once any sign-in retries are exhausted."""

    result_text: str
    is_error: bool
    # ``{"slug", "path"}`` when the tool touched a workspace file. None otherwise.
    link: dict[str, Any] | None = None


@dataclass(slots=True)
class RoundCapReached:
    """The tool-round budget was exhausted without a final answer."""

    max_tool_rounds: int


@dataclass(slots=True)
class ApprovalsRequested:
    """The round's remaining calls wait on the user, so the turn parks here."""

    items: list[approval_service.PendingCall]


TurnEvent = (
    AssistantTextDelta
    | AssistantReasoningDelta
    | AssistantFinalTurn
    | AssistantToolCallsTurn
    | ToolResultTurn
    | ToolAuthRequired
    | ApprovalsRequested
    | RoundCapReached
)
