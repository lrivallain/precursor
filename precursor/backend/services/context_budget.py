"""Approximate token budgeting for LLM prompts.

The chat + scheduled-run tool loop appends every MCP tool result to the
in-memory ``messages`` list, and history is loaded with no cap. A few large
file reads or fetches across several tool rounds can push the prompt past the
model's context window (e.g. "prompt is too long: 1.29M > 1M tokens").

This module trims the ``messages`` list to a token budget right before each
provider call, without mutating the caller's list. Strategy:

1. Truncate any single oversized message (mainly tool results) to a per-message
   cap so one giant payload can't dominate the prompt.
2. Always keep ``system`` messages and as many of the *most recent* turns as fit
   the overall budget, dropping the oldest first.
3. Strip leading orphan ``tool`` messages so we never send a tool result whose
   parent assistant tool-call turn was dropped (providers reject that).

Token counts are estimated from character length (no tokenizer dependency);
the estimate intentionally runs slightly high so we trim a bit early rather
than overflow.
"""

from __future__ import annotations

import json
from dataclasses import replace

from precursor.backend.services.llm.base import ChatMessage

# Conservative chars-per-token estimate. Real ratios are ~3.5-4 for prose and
# lower for code/JSON; using 3 biases the estimate high (trim earlier).
_CHARS_PER_TOKEN = 3
# Fixed per-message overhead (role, delimiters) the provider adds.
_MESSAGE_OVERHEAD_TOKENS = 8


def estimate_tokens(message: ChatMessage) -> int:
    """Rough token estimate for a single message."""
    chars = len(message.content or "")
    if message.tool_calls:
        chars += len(json.dumps(message.tool_calls, default=str))
    for url in message.image_urls:
        chars += len(url)
    return chars // _CHARS_PER_TOKEN + _MESSAGE_OVERHEAD_TOKENS


def estimate_total_tokens(messages: list[ChatMessage]) -> int:
    return sum(estimate_tokens(m) for m in messages)


def _truncate_content(text: str, max_tokens: int) -> str:
    max_chars = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    dropped = len(text) - max_chars
    return text[:max_chars] + f"\n\n…[truncated {dropped} characters]"


def trim_messages(
    messages: list[ChatMessage],
    *,
    max_input_tokens: int,
    per_message_max_tokens: int,
) -> list[ChatMessage]:
    """Return a budget-trimmed copy of ``messages`` (inputs are not mutated).

    ``system`` messages are always retained (but still subject to per-message
    truncation). Among the rest, the newest turns are kept up to
    ``max_input_tokens``; the oldest are dropped first.
    """
    if not messages:
        return messages

    # 1. Per-message truncation (copy; never mutate the caller's objects).
    capped: list[ChatMessage] = []
    for m in messages:
        if m.content and len(m.content) > per_message_max_tokens * _CHARS_PER_TOKEN:
            capped.append(replace(m, content=_truncate_content(m.content, per_message_max_tokens)))
        else:
            capped.append(m)

    system = [m for m in capped if m.role == "system"]
    rest = [m for m in capped if m.role != "system"]

    budget = max_input_tokens - estimate_total_tokens(system)

    # 2. Keep the most recent messages that fit the remaining budget.
    kept_reversed: list[ChatMessage] = []
    used = 0
    for m in reversed(rest):
        cost = estimate_tokens(m)
        if kept_reversed and used + cost > budget:
            break
        used += cost
        kept_reversed.append(m)
    kept = list(reversed(kept_reversed))

    # 3. Drop leading orphan tool results (their assistant parent was dropped).
    while kept and kept[0].role == "tool":
        kept.pop(0)

    return system + kept
