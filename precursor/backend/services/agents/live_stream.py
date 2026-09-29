"""What a live run is streaming right now, before any of it is archived.

Streaming frames (``LIVE_ONLY_KINDS``) are never archived: the complete message,
reasoning and tool events that follow carry their content. Until those land,
this is the only place a round's thinking and answer exist, so the live views
(the agent timeline's "Thinking…" row, the dashboard card, a workflow's active
step) read them here.
"""

from __future__ import annotations

from dataclasses import dataclass

from precursor.backend.schemas.agent import AgentEvent
from precursor.backend.services.agents.event_normalizer import TEXT_CAP

# The streamed answer only feeds the one-line narration, which needs its opening
# sentence; stop accumulating once we clearly have it.
MESSAGE_CAP = 400

# A live card previews the latest thinking step, which sits at the end.
THINKING_PREVIEW_CAP = 1000

# SDK events that open or close a round (or the whole turn).
_BOUNDARIES = frozenset({"turn_start", "turn_end", "idle", "aborted"})


@dataclass
class StreamUpdate:
    # The live view changed, so an open timeline or card should refresh.
    changed: bool = False
    # Thinking that streamed but whose complete event carried no text, or never
    # came (the turn was stopped): archive it in place so it isn't lost.
    flush: AgentEvent | None = None


@dataclass
class LiveStream:
    # The current round's thinking so far. The SDK sends the complete block only
    # after the round's message, so this covers the whole time it is thinking.
    reasoning: str = ""
    # The model has moved on to its answer or a tool call: it is done thinking,
    # even though the complete reasoning event hasn't landed yet.
    answering: bool = False
    # The opening of the answer being streamed, for the live narration.
    message: str = ""
    # Monotonic times of the last activity stamp and publish for a streaming
    # frame, so a stream of them doesn't cost a DB write and a bus message each.
    touched: float = 0.0
    published: float = 0.0
    # A change the publish throttle held back.
    dirty: bool = False

    def observe(self, event: AgentEvent) -> StreamUpdate:
        kind = event.kind
        if kind == "reasoning_delta":
            if not event.text:
                return StreamUpdate()
            self.reasoning = (self.reasoning + event.text)[-TEXT_CAP:]
            self.answering = False
            return StreamUpdate(changed=True)
        if kind == "assistant_delta":
            was_thinking = not self.answering
            self.answering = True
            if event.text and len(self.message) < MESSAGE_CAP:
                self.message += event.text
                return StreamUpdate(changed=True)
            return StreamUpdate(changed=was_thinking and bool(self.reasoning))
        if kind == "AssistantToolCallDeltaData":
            was_thinking = not self.answering
            self.answering = True
            return StreamUpdate(changed=was_thinking and bool(self.reasoning))
        if kind == "assistant_message":
            self.message = ""
            self.answering = True
            return StreamUpdate()
        if kind == "reasoning":
            streamed = self._take_reasoning()
            if streamed and not (event.text or "").strip():
                return StreamUpdate(changed=True, flush=self._as_event(streamed, event))
            return StreamUpdate(changed=bool(streamed))
        if kind in _BOUNDARIES:
            streamed = self._take_reasoning()
            self.message = ""
            if streamed:
                return StreamUpdate(changed=True, flush=self._as_event(streamed, event))
        return StreamUpdate()

    @property
    def thinking(self) -> str | None:
        """The latest of the thinking in progress, while the model is thinking."""
        if not self.reasoning or self.answering:
            return None
        return self.reasoning[-THINKING_PREVIEW_CAP:]

    def _take_reasoning(self) -> str:
        streamed = self.reasoning if self.reasoning.strip() else ""
        self.reasoning = ""
        self.answering = False
        return streamed

    @staticmethod
    def _as_event(text: str, source: AgentEvent) -> AgentEvent:
        return AgentEvent(
            kind="reasoning", text=text, at=source.at, agent_run_id=source.agent_run_id
        )
