"""The agent text protocol: slash commands, control directives and artifacts.

Agents steer their own lifecycle and hand results on to workflows by emitting
plain text lines (``PROGRESS:``, ``NEED_INPUT:``, ``OBJECTIVE_COMPLETE:``,
``ARTIFACT:`` … ``END_ARTIFACT``). Both halves of that contract live here — the
prompt text that teaches it and the parsers that read it back — so they can't
drift apart, and so callers that only parse text (the workflow coordinator, the
routers) don't have to import the SDK-facing manager.

Pure string processing, and a leaf module: it must never import
``services.agents.manager``.
"""

from __future__ import annotations

import re
from typing import Any

# Slash commands the system intercepts inside an agent session map to real actions
# (rename/clear/archive) handled in ``AgentManager.run_command`` rather than being
# forwarded to the SDK as prompt text. Every *other* slash command is rejected.
_SLASH_RE = re.compile(r"^/([a-zA-Z][a-zA-Z0-9-]*)\s*([\s\S]*)$")


def parse_agent_command(message: str) -> tuple[str, str] | None:
    """Recognise a leading slash command in a message sent to an agent.

    Returns ``(name, argument)`` for *any* ``/word …`` input (so the caller can
    reject unknown commands instead of leaking them to the SDK), or ``None`` when
    the text is a normal message.
    """
    text = message.lstrip()
    if not text.startswith("/"):
        return None
    match = _SLASH_RE.match(text)
    if not match:
        return None
    return match.group(1).lower(), match.group(2).strip()


# When an autonomous agent finishes a turn without declaring completion, we nudge
# it to take the next step toward its objective with this message. It's phrased so
# the model keeps pursuing the durable goal rather than treating it as a new task.
_CONTINUE_NUDGE = (
    "Continue working autonomously toward your objective. Narrate what you're "
    "about to do in one short plain sentence, then take the next concrete step "
    "now. When the objective is fully met, reply with a line "
    "'OBJECTIVE_COMPLETE: <2-3 sentence summary>'. If you are blocked on a "
    "decision only the human can make, reply with 'NEED_INPUT: <your question>'. "
    "Otherwise keep going and report progress several times across the run with "
    "'PROGRESS: <0-100> | <what you just did>'. Publish durable results other "
    "agents may need — one as you finish each phase — with 'ARTIFACT: <title> | "
    "<content>' for a short value, or a multi-line block 'ARTIFACT: <title>' … "
    "'END_ARTIFACT' for a substantial deliverable so its full body is captured."
)

# Appended to an autonomous agent's system preamble. It teaches the sentinel
# protocol the goal loop reads back — the agent controls its own lifecycle by
# emitting these lines, so it can run unattended and only pull the human in when
# it genuinely needs a decision.
_AUTONOMY_PROTOCOL = (
    "You are running in AUTONOMOUS mode. Your task above is a durable OBJECTIVE, "
    "not a single question: keep working toward it across multiple turns without "
    "waiting to be prompted each time. After each step you will be nudged to "
    "continue automatically. As you work, narrate what you're doing in one short "
    "plain sentence before each action, so the human can follow along live from "
    "the dashboard.\n\n"
    "Use these control lines to steer your own lifecycle (put each on its own "
    "line, exactly as shown):\n"
    "- 'PROGRESS: <0-100> | <what you just accomplished>' — report several times "
    "across the run (early, middle, and late — not only at the end) so the human "
    "can watch from the dashboard.\n"
    "- 'NEED_INPUT: <question>' — only when you are truly blocked on a decision "
    "or approval that only the human can give. You will pause until they answer.\n"
    "- 'OBJECTIVE_COMPLETE: <2-3 sentence summary>' — when the objective is fully "
    "met. This ends the mission.\n\n"
    "Share durable outputs with the rest of the fleet using ARTIFACT directives "
    "so agents that depend on you receive them as their input; publish one as you "
    "finish each phase or reach a finding.\n"
    "- For a short single-line value: 'ARTIFACT: <title> | <content>'.\n"
    "- For a SUBSTANTIAL or multi-line deliverable (an inventory, a draft, a "
    "review), always use a block so nothing is truncated: put the title on the "
    "ARTIFACT line with NO pipe, then the full Markdown body on the following "
    "lines, then a closing 'END_ARTIFACT' line. For example:\n"
    "    ARTIFACT: Release notes\n"
    "    ## Highlights\n"
    "    - First thing\n"
    "    - Second thing\n"
    "    END_ARTIFACT\n"
    "Put the ENTIRE deliverable inside the artifact (inline body or block) — it "
    "is the real output other agents and the human consume, so never leave it "
    "only in your surrounding prose, and do not append PROGRESS/OBJECTIVE_COMPLETE "
    "onto the artifact body.\n\n"
    "Prefer making progress over asking. Don't ask for confirmation on steps you "
    "can safely take yourself. Stop only when complete or genuinely blocked. The "
    "control lines above are required output: always emit the relevant one even "
    "when base guidance would have you end tersely without a status or recap, "
    "since the system reads them to follow and resurface your mission."
)

# Sentinel directives an autonomous agent embeds in its assistant messages to
# drive its own lifecycle. Parsed only when ``autonomy_enabled`` so a normal
# agent that happens to type these words is unaffected.
#
# Anchored to the *start of a line* (``re.M``) so a directive quoted or explained
# mid-sentence in prose — e.g. an agent narrating "I don't need to emit
# **NEED_INPUT:** to your dashboard" — never misfires and falsely blocks the run.
# ``_DIR_LEAD`` tolerates leading markdown/quote decoration (blockquote, list
# marker, bold/italic, inline code) on the directive line; ``_DIR_POST`` eats the
# closing emphasis of a ``**LABEL:**`` so a stray ``**`` doesn't leak into — and
# unbalance the Markdown of — the captured question/summary.
_DIR_LEAD = r"^[ \t>*_`-]*"
_DIR_POST = r"[ \t*_`]*"
_DIRECTIVE_COMPLETE_RE = re.compile(
    _DIR_LEAD + r"OBJECTIVE[_ ]COMPLETE\s*:" + _DIR_POST + r"(.+)", re.I | re.M
)
_DIRECTIVE_NEED_INPUT_RE = re.compile(
    _DIR_LEAD + r"NEED[_ ]INPUT\s*:" + _DIR_POST + r"(.+)", re.I | re.M
)
_DIRECTIVE_PROGRESS_RE = re.compile(
    _DIR_LEAD + r"PROGRESS\s*:\s*(\d{1,3})\s*(?:\|\s*(.+))?", re.I | re.M
)
# Publish a durable named output to the shared fleet blackboard. Two shapes are
# accepted (see ``_extract_artifacts``): a one-line ``ARTIFACT: <title> | <body>``
# for short values, and a multi-line block that starts with ``ARTIFACT: <title>``
# (no pipe) and runs until an ``END_ARTIFACT`` terminator or the next directive —
# so a substantial deliverable (a list, a draft, a review) is captured whole.
_ARTIFACT_HEADER_RE = re.compile(r"^\s*ARTIFACT\s*:\s*(.*)$", re.I)
_ARTIFACT_END_RE = re.compile(r"^\s*(?:END[_ ]ARTIFACT|/ARTIFACT|ARTIFACT[_ ]END)\s*$", re.I)

# An ordered-list marker (" 1. ", "2. ", …) with a following space, anchored to a
# word boundary so decimals/prices/versions like "3.50" or "v2.0" (no space after
# the dot) are never matched.
_ORDERED_MARKER_RE = re.compile(r"(?:(?<=\s)|^)(\d{1,2})\.\s")


def _split_inline_ordered_list(text: str) -> str:
    """Break a numbered list packed onto one physical line into separate lines.

    A single ``ARTIFACT:`` directive is one line, so a model that writes
    "1. a 2. b 3. c" yields a run-on Markdown paragraph. We split *only* a
    strictly sequential ``1, 2, 3, …`` run so incidental "2." tokens (decimals,
    versions, prices) are left untouched.
    """
    markers = list(_ORDERED_MARKER_RE.finditer(text))
    nums = [int(m.group(1)) for m in markers]
    if len(nums) < 2 or nums != list(range(1, len(nums) + 1)):
        return text
    pieces: list[str] = []
    prev = 0
    for i, m in enumerate(markers):
        if i == 0:
            continue
        pieces.append(text[prev : m.start()].rstrip())
        prev = m.start()
    pieces.append(text[prev:])
    return "\n".join(p for p in pieces if p).strip()


def _normalize_artifact_content(content: str) -> str:
    """Coax single-line ``ARTIFACT:`` content into well-formed Markdown.

    A model can't press Enter inside a one-line directive, so multi-line
    deliverables (lists, paragraphs) collapse. We let it express breaks with an
    escaped ``\\n`` (unescaped here) and, as a safety net, break a packed
    sequential inline numbered list onto its own lines so it renders as a real
    list instead of a run-on line.
    """
    if "\\n" in content or "\\t" in content or "\\r" in content:
        content = (
            content.replace("\\r\\n", "\n")
            .replace("\\r", "\n")
            .replace("\\n", "\n")
            .replace("\\t", "\t")
        )
    if "\n" not in content:
        content = _split_inline_ordered_list(content)
    return content


# Control directives never read as "what the agent is doing" — skip them when
# distilling a live narration line so a mission's control channel doesn't leak
# into the dashboard's plain-language activity hint.
_NARRATION_SKIP_RE = re.compile(
    r"^\s*(PROGRESS|NEED[_ ]INPUT|OBJECTIVE[_ ]COMPLETE|ARTIFACT)\s*:", re.I
)


def _clean_narration(text: str) -> str | None:
    """Distil an assistant message into a one-line "what it's doing now" label.

    The Copilot base prompt has the model emit short natural-language *commentary
    preambles* before it acts (e.g. "Let me check the migration script"). Those
    arrive as ordinary assistant text; surfacing the first meaningful line as a
    live narration makes a working agent far more monitorable from the dashboard
    than a bare tool name. We take the first prose line, drop control directives
    and light markdown noise, and cap the length.
    """
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _NARRATION_SKIP_RE.match(line):
            continue
        line = re.sub(r"^[#>*\-\s]+", "", line)  # leading heading/list markers
        line = re.sub(r"[*_`]+", "", line).strip()  # inline emphasis/code ticks
        if line:
            return line[:160]
    return None


def _strip_trailing_directives(content: str) -> str:
    """Drop trailing control-directive lines a model glued onto artifact content.

    A model sometimes appends its ``OBJECTIVE_COMPLETE:``/``PROGRESS:`` line to
    the same inline ``ARTIFACT:`` body (often via an escaped ``\\n``), so the
    published artifact ends with a stray control line. We peel those off the tail
    so the stored deliverable is just the deliverable.
    """
    lines = content.split("\n")
    while lines and (not lines[-1].strip() or _NARRATION_SKIP_RE.match(lines[-1])):
        lines.pop()
    return "\n".join(lines).strip()


# How much of a turn's answer we keep in ``result_summary``. This is a *display*
# budget — the column feeds the agent list and the run cards, where an unbounded
# body would be unreadable — not a limit on what the agent produced. The full
# message stays in the durable event archive, so any consumer that needs the
# whole thing (the topic repost, a workflow step's trace) reads it back from
# there rather than inheriting this cut. See
# ``precursor.backend.services.agents.workflow._step_output``.
RESULT_SUMMARY_CAP = 2000

# Whole-line control directives (anywhere in the text) plus an ``ARTIFACT`` block
# terminator. Used to scrub a value that will be *shown to the user* as a result,
# so the agent's control channel never leaks into its displayed deliverable.
_CONTROL_LINE_RE = re.compile(
    r"^[ \t>*_`-]*(?:OBJECTIVE[_ ]COMPLETE|NEED[_ ]INPUT|PROGRESS|ARTIFACT|"
    r"END[_ ]ARTIFACT|/ARTIFACT|ARTIFACT[_ ]END)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)


def strip_control_directives(text: str) -> str:
    """Remove control-directive lines from a value surfaced to the user.

    Directives (``OBJECTIVE_COMPLETE`` / ``NEED_INPUT`` / ``PROGRESS`` /
    ``ARTIFACT`` …) are the agent's control channel, not part of the deliverable.
    We keep the raw assistant message for parsing, forwarding, and gate verdicts,
    but scrub these tokens from anything stored as a displayed *result* so a step's
    output reads as the work itself — not the plumbing that produced it.
    """
    cleaned = _CONTROL_LINE_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)  # collapse blank runs the removal left
    return cleaned.strip()


def _extract_artifacts(text: str) -> list[dict[str, str]]:
    """Pull every published artifact from an assistant message.

    Supports two shapes so a substantial deliverable is never truncated:

    * **Inline** — ``ARTIFACT: <title> | <body>`` on one line, for short values.
    * **Block** — a line ``ARTIFACT: <title>`` with no ``|``, then the full
      Markdown body on the following lines, terminated by an ``END_ARTIFACT``
      line, the next control directive, or end of message. This is what lets a
      research inventory, a draft, or a review land whole rather than as a bare
      heading with the real content stranded in prose.
    """
    lines = text.splitlines()
    artifacts: list[dict[str, str]] = []
    i, n = 0, len(lines)
    while i < n:
        header = _ARTIFACT_HEADER_RE.match(lines[i])
        if header is None:
            i += 1
            continue
        rest = header.group(1).strip()
        if "|" in rest:  # inline: 'title | body' on this single line
            title, _, body = rest.partition("|")
            title, body = title.strip(), _normalize_artifact_content(body.strip())
            i += 1
        else:  # block: 'ARTIFACT: title' then body lines until a terminator
            title = rest
            i += 1
            collected: list[str] = []
            while i < n:
                if _ARTIFACT_END_RE.match(lines[i]):
                    i += 1
                    break
                if _NARRATION_SKIP_RE.match(lines[i]):  # next directive ends it
                    break
                collected.append(lines[i])
                i += 1
            body = "\n".join(collected).strip()
        body = _strip_trailing_directives(body)
        if title and body:
            artifacts.append({"title": title[:200], "content": body[:100000]})
    return artifacts


def parse_agent_directives(text: str | None) -> dict[str, Any]:
    """Extract autonomy control directives from an assistant message.

    Returns a dict that may contain ``complete`` (summary str), ``blocked``
    (question str), ``progress`` (``{"value": int, "label": str | None}``),
    and/or ``artifacts`` (``list[{"title": str, "content": str}]``).
    Completion and a raised question are mutually exclusive in effect (completion
    wins), but progress and artifacts can accompany either. Empty dict when
    nothing matched.
    """
    result: dict[str, Any] = {}
    if not text:
        return result
    if (m := _DIRECTIVE_COMPLETE_RE.search(text)) is not None:
        result["complete"] = m.group(1).strip()
    if (m := _DIRECTIVE_NEED_INPUT_RE.search(text)) is not None:
        result["blocked"] = m.group(1).strip()
    if (m := _DIRECTIVE_PROGRESS_RE.search(text)) is not None:
        value = max(0, min(100, int(m.group(1))))
        label = (m.group(2) or "").strip() or None
        result["progress"] = {"value": value, "label": label}
    artifacts = _extract_artifacts(text)
    if artifacts:
        result["artifacts"] = artifacts
    return result
