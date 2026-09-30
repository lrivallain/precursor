"""Approximate token budgeting for LLM prompts.

The chat + scheduled-run tool loop appends every MCP tool result to the
in-memory ``messages`` list, and history is loaded with no cap. A few large
file reads or fetches across several tool rounds can push the prompt past the
model's context window (e.g. "prompt is too long: 1.29M > 1M tokens").

This module trims the ``messages`` list to a token budget right before each
provider call, without mutating the caller's list. Strategy:

1. Truncate any single oversized tool result to a per-message cap so one giant
   payload can't dominate the prompt. User turns are exempt: their document
   attachments already carry their own cap (``llm_max_attachment_chars``).
2. Always keep ``system`` messages and as many of the *most recent* turns as fit
   the overall budget, dropping the oldest first.
3. Strip leading orphan ``tool`` messages so we never send a tool result whose
   parent assistant tool-call turn was dropped (providers reject that).

Token counts are estimated from character length (no tokenizer dependency);
the estimate intentionally runs slightly high so we trim a bit early rather
than overflow.

Two cheap hygiene passes run before any of that, neither calling a model:

* :func:`strip_inline_binaries` drops base64 payloads (an MCP ``image`` block's
  ``data``, a resource ``blob``, a ``data:`` URI) that tool results carry as
  text. The model can't see a picture through its base64, so it is pure noise —
  one browser screenshot is ~150k characters.
* :func:`elide_stale_tool_results` shortens large tool results from older user
  turns to a preview; the model can call the tool again when it needs the rest.
"""

from __future__ import annotations

import json
import re
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


# A JSON ``"data"``/``"blob"`` string made of base64 only, long enough that it
# can't be meaningful prose. JSON-escaped newlines (MIME-wrapped base64) allowed.
_B64_FIELD_RE = re.compile(r'"(data|blob)"(\s*:\s*)"((?:[A-Za-z0-9+/=]|\\n){512,})"')
_DATA_URI_RE = re.compile(r"data:([\w.+-]+/[\w.+-]+);base64,[A-Za-z0-9+/=]{512,}")
# Tool results at or below this size are kept verbatim however old they are:
# eliding them would save little and cost the model real detail.
STALE_TOOL_RESULT_MIN_CHARS = 2_000
_STALE_PREVIEW_CHARS = 400


def _kb(chars: int) -> str:
    # base64 carries 3 bytes per 4 characters.
    return f"~{max(1, round(chars * 3 / 4 / 1024))} KB"


def strip_inline_binaries(text: str) -> str:
    """Replace base64 payloads embedded in ``text`` with a short placeholder."""
    if len(text) < 512:
        return text

    def _field(m: re.Match[str]) -> str:
        return f'"{m.group(1)}"{m.group(2)}"[binary omitted: {_kb(len(m.group(3)))}]"'

    def _uri(m: re.Match[str]) -> str:
        return f"data:{m.group(1)};base64,[omitted: {_kb(len(m.group(0)))}]"

    return _DATA_URI_RE.sub(_uri, _B64_FIELD_RE.sub(_field, text))


def elide_stale_tool_results(messages: list[ChatMessage], *, keep_turns: int) -> list[ChatMessage]:
    """Shorten large tool results older than the last ``keep_turns`` user turns.

    Returns a new list; ``messages`` is not mutated. ``keep_turns <= 0`` is a
    no-op. The tool call/result pairing is untouched, only the content shrinks.
    """
    if keep_turns <= 0:
        return messages
    seen_users = 0
    cutoff = -1  # index of the oldest user turn still kept in full
    for idx in range(len(messages) - 1, -1, -1):
        if messages[idx].role == "user":
            seen_users += 1
            if seen_users == keep_turns:
                cutoff = idx
                break
    if cutoff <= 0:
        return messages
    out = list(messages)
    for idx in range(cutoff):
        m = out[idx]
        if m.role != "tool" or len(m.content or "") <= STALE_TOOL_RESULT_MIN_CHARS:
            continue
        content = m.content
        tool = f"`{m.name}` " if m.name else ""
        out[idx] = replace(
            m,
            content=(
                content[:_STALE_PREVIEW_CHARS]
                + f"\n\n…[older {tool}result elided to save context ({len(content):,}"
                " characters). Call the tool again if you need the full output.]"
            ),
        )
    return out


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

    ``tool`` results are truncated to ``per_message_max_tokens``. ``system``
    messages are always retained. Among the rest, the newest turns are kept up
    to ``max_input_tokens``; the oldest are dropped first.
    """
    if not messages:
        return messages

    # 1. Per-message truncation (copy; never mutate the caller's objects).
    capped: list[ChatMessage] = []
    for m in messages:
        if m.role == "tool" and m.content:
            stripped = strip_inline_binaries(m.content)
            if len(stripped) != len(m.content):
                m = replace(m, content=stripped)
        if (
            m.role == "tool"
            and m.content
            and len(m.content) > per_message_max_tokens * _CHARS_PER_TOKEN
        ):
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
