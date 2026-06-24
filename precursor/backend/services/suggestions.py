"""Suggested follow-up replies — parse the model's trailing ``suggest`` block.

Approach: the system prompt invites the model to end a reply with a fenced code
block tagged ``suggest`` listing short follow-up actions. We parse that block out
of the final assistant text server-side, strip it from the persisted/displayed
content, and surface the options as structured data the UI renders as clickable
chips. Keeping the parse on the backend means a clean transcript and one
contract shared by topics, chats, workspaces, and agents.
"""

from __future__ import annotations

import re

# Hard cap so a misbehaving model can't flood the UI with chips.
MAX_SUGGESTIONS = 5

# Match a single ``suggest`` fenced block anchored at the very end of the text.
# DOTALL lets the body span lines; excluding ``` from the body keeps the match
# self-contained so only the final block is ever lifted (an earlier stray
# ``suggest`` block stays in the visible transcript). The trailing ``$`` (on a
# right-stripped string) anchors to the end of the message.
_SUGGEST_BLOCK_RE = re.compile(
    r"\n*```suggest[^\n]*\n(?P<body>(?:(?!```).)*?)\n?```$",
    re.DOTALL | re.IGNORECASE,
)

# Strip a leading Markdown list marker ("- ", "* ", "1. ", "1) ") from a line.
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")

# Inviting (not mandating) the block keeps replies clean when no follow-up makes
# sense. Phrased so options read as the user's own next message.
SUGGESTIONS_INSTRUCTION = (
    "Suggested follow-ups: after your reply you MAY offer up to "
    f"{MAX_SUGGESTIONS} short actions the user is likely to want next. When you "
    "do, end your message with a fenced code block tagged `suggest`, one option "
    "per line as a Markdown list item. Keep each option under ~8 words, phrased "
    "as the user would say it (imperative or first person). Omit the block "
    "entirely when no follow-up is useful. Example:\n"
    "```suggest\n- Show me the failing tests\n- Explain the root cause\n```"
)


def split_suggestions(text: str) -> tuple[str, list[str]]:
    """Split a trailing ``suggest`` block off ``text``.

    Returns ``(clean_text, suggestions)``. When no block is present the original
    text is returned unchanged with an empty list. Suggestions are de-marked,
    trimmed, de-duplicated (order-preserving) and capped at ``MAX_SUGGESTIONS``.
    """
    if not text:
        return text, []

    stripped = text.rstrip()
    match = _SUGGEST_BLOCK_RE.search(stripped)
    if match is None:
        return text, []

    clean = stripped[: match.start()].rstrip()

    items: list[str] = []
    seen: set[str] = set()
    for raw_line in match.group("body").splitlines():
        line = _LIST_MARKER_RE.sub("", raw_line.strip()).strip()
        if not line or line in seen:
            continue
        seen.add(line)
        items.append(line)
        if len(items) >= MAX_SUGGESTIONS:
            break

    # A block that parsed to nothing usable is dropped along with its markup.
    return clean, items
