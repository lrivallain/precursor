"""Map raw Copilot SDK events onto Precursor's workflow-timeline shape.

Extracted from the agent manager: these are pure transforms with no session
state, so they live as free functions the manager delegates to.
"""

from __future__ import annotations

import json
from typing import Any

from precursor.backend.schemas.agent import AgentEvent

# Cap on any single captured tool result / error string folded into a timeline
# node, so a huge tool output can't bloat the archived event payload.
TOOL_RESULT_CAP = 4000

# Map SDK event class names → coarse workflow step kinds for the UI.
_EVENT_KINDS: dict[str, str] = {
    "AssistantMessageData": "assistant_message",
    "AssistantMessageDeltaData": "assistant_delta",
    "AssistantReasoningData": "reasoning",
    "AssistantReasoningDeltaData": "reasoning_delta",
    "AssistantTurnStartData": "turn_start",
    "AssistantTurnEndData": "turn_end",
    "AssistantUsageData": "usage",
    "SessionUsageInfoData": "context_usage",
    "SessionIdleData": "idle",
    "AbortData": "aborted",
}


def unwrap_result(value: Any) -> Any:
    """Pull readable text out of SDK result wrappers.

    Tool results arrive as ``ToolExecutionCompleteResult`` objects whose
    repr would otherwise leak into the UI; surface their content instead.
    """
    if isinstance(value, str):
        return value
    for attr in ("content", "detailed_content"):
        inner = getattr(value, attr, None)
        if isinstance(inner, str) and inner.strip():
            return inner
    return value


def jsonify(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str, indent=2)
    except (TypeError, ValueError):
        return str(value)


def normalize_event(event: Any) -> AgentEvent:
    """Map a raw SDK event onto the workflow-timeline shape."""
    data = getattr(event, "data", event)
    name = type(data).__name__
    # ``message`` covers error events (SessionErrorData/ErrorData) whose detail
    # lives there rather than in ``content``/``text`` — otherwise their
    # timeline node renders blank.
    text = (
        getattr(data, "content", None)
        or getattr(data, "text", None)
        or getattr(data, "message", None)
    )
    tool_name = getattr(data, "tool_name", None) or getattr(data, "name", None)
    kind = _EVENT_KINDS.get(name, name)
    tool_status: str | None = None
    if "ToolRequest" in name or kind == "tool_call":
        tool_status = "running"
    elif "ToolResult" in name or kind == "tool_result":
        tool_status = "error" if getattr(data, "is_error", False) else "done"
    # Capture tool I/O (and error diagnostics) so the UI can show "what was
    # done" / "why it failed" on demand.
    extra: dict[str, Any] = {}
    for attr in (
        "arguments",
        "input",
        "result",
        "output",
        "server_name",
        "error_type",
        "error_code",
        "status_code",
    ):
        val = getattr(data, attr, None)
        if val is None:
            continue
        if attr in ("result", "output"):
            val = unwrap_result(val)
        if isinstance(val, str):
            extra[attr] = val[:TOOL_RESULT_CAP]
        else:
            extra[attr] = jsonify(val)
    # ``ToolExecutionCompleteData`` reports success + result/error as nested
    # objects rather than the flat ``is_error``/``result`` string attrs the
    # loop above looks for, so a *failed* tool would otherwise archive as
    # ``data: null`` with no status — losing the reason the agent hit a wall
    # (e.g. a sandbox "permission denied" or a fetch error). Pull them out
    # explicitly so the timeline shows why a tool call failed.
    if name == "ToolExecutionCompleteData":
        success = getattr(data, "success", None)
        if success is not None:
            tool_status = "done" if success else "error"
            extra["success"] = bool(success)
        sandboxed = getattr(data, "sandboxed", None)
        if sandboxed is not None:
            # Surfaces that a command ran in the ephemeral cmd-runner jail —
            # key context when file writes silently don't persist.
            extra["sandboxed"] = bool(sandboxed)
        err = getattr(data, "error", None)
        if err is not None:
            message = getattr(err, "message", None) or str(err)
            extra["error"] = str(message)[:TOOL_RESULT_CAP]
            code = getattr(err, "code", None)
            if code:
                extra["error_code"] = str(code)
        if "result" not in extra:
            content = unwrap_result(getattr(data, "result", None))
            if isinstance(content, str) and content.strip():
                extra["result"] = content[:TOOL_RESULT_CAP]
        if not tool_name:
            desc = getattr(data, "tool_description", None)
            tool_name = getattr(desc, "name", None) or getattr(desc, "tool_name", None)
    # Usage events carry token counts, not tool I/O. Capture them verbatim
    # (as raw ints, not JSON-stringified) so the workflow timeline can drive
    # the per-agent usage stats in the side panel: ``AssistantUsageData``
    # meters each LLM round, ``SessionUsageInfoData`` reports the live
    # context-window occupancy.
    if name == "AssistantUsageData":
        for attr in ("input_tokens", "output_tokens", "reasoning_tokens"):
            val = getattr(data, attr, None)
            if val is not None:
                extra[attr] = int(val)
        # The resolved model for this LLM round (a required SDK field). Lets
        # the UI show the concrete model per turn — useful for default-model
        # agents whose session.model is null.
        model = getattr(data, "model", None)
        if model:
            extra["model"] = str(model)
    elif name == "SessionUsageInfoData":
        for attr in ("current_tokens", "token_limit", "conversation_tokens"):
            val = getattr(data, attr, None)
            if val is not None:
                extra[attr] = int(val)
    return AgentEvent(
        kind=kind,
        text=str(text) if text is not None else None,
        tool_name=str(tool_name) if tool_name else None,
        tool_status=tool_status,
        request_id=getattr(data, "tool_call_id", None),
        data=extra or None,
    )
