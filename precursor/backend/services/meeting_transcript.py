"""Scrape a linked Teams meeting's transcript via the WorkIQ MCP server.

The Live "Summary" tab can build a recap from the *Teams* meeting transcript
instead of a locally-captured recording — a "no local record" path. There's no
direct Graph integration in Precursor; we call the built-in ``workiq`` MCP server
(Microsoft 365) programmatically through the MCP client manager, exactly like
``meeting_agenda``.

The Graph flow (delegated, needs ``OnlineMeetingTranscript.Read.All`` and the
user to be the meeting organizer):

1. Resolve the online meeting from the linked event's Teams join URL:
   ``GET /me/onlineMeetings?$filter=JoinWebUrl eq '{joinUrl}'``.
2. List its transcripts: ``GET /me/onlineMeetings/{id}/transcripts`` — pick the
   most recent by ``createdDateTime``.
3. Fetch the content as WebVTT:
   ``GET /me/onlineMeetings/{id}/transcripts/{tid}/content?$format=text/vtt``.
4. Parse the VTT ``<v Speaker>text</v>`` cues into readable ``Speaker: text``
   lines for the summary prompt.

Best-effort and fail-closed: if WorkIQ isn't configured/authenticated, the
meeting has no join URL, or no transcript exists yet, we return
``available=False`` with a human detail so the UI degrades gracefully.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from precursor.backend.services.meeting_agenda import _result_to_json

logger = logging.getLogger(__name__)

WORKIQ_SERVER = "workiq"

# Keep the transcript we feed the summariser bounded (mirrors the summary's own
# transcript budget); a long meeting is trimmed to the most recent lines.
_TRANSCRIPT_CHARS = 24000


def _rows_from(data: Any) -> list[dict[str, Any]]:
    """Extract Graph collection rows from WorkIQ's ``fetch`` response shapes.

    ``fetch`` wraps results as ``{"results": [{"data": {...}, "statusCode": 200}]}``
    where ``data`` is the Graph payload (a ``{value: [...]}`` collection or a
    single entity). Tolerates a bare Graph payload or a plain list too.
    """
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        out: list[dict[str, Any]] = []
        for res in data["results"]:
            if isinstance(res, dict) and res.get("statusCode") in (None, 200):
                out.extend(_rows_from(res.get("data")))
        return out
    if isinstance(data, dict):
        value = data.get("value")
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
        return [data]
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def _content_from(data: Any) -> str:
    """Pull the raw VTT text out of a WorkIQ ``fetch`` result for a ``/content``
    endpoint. WorkIQ returns the stream as ``data`` (a string, or occasionally a
    ``{content: "..."}`` wrapper)."""
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        parts: list[str] = []
        for res in data["results"]:
            if isinstance(res, dict) and res.get("statusCode") in (None, 200):
                parts.append(_content_from(res.get("data")))
        return "\n".join(p for p in parts if p)
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("content", "value", "text"):
            v = data.get(key)
            if isinstance(v, str):
                return v
    return ""


# One VTT cue's speaker voice tag: ``<v Alex Kim>Hello everyone</v>`` — capture
# the name and the spoken text; the closing tag is optional in some exports.
_VOICE_RE = re.compile(r"<v\s+([^>]+)>(.*?)(?:</v>|$)", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def parse_vtt(vtt: str) -> str:
    """Turn Teams WebVTT into readable ``Speaker: line`` text.

    Drops the ``WEBVTT`` header, cue ids and ``00:00 --> 00:05`` timing lines,
    keeps only the spoken cue text, and prefixes each with its ``<v …>`` speaker.
    Collapses consecutive lines from the same speaker into one block.
    """
    lines: list[tuple[str | None, str]] = []
    for block in re.split(r"\n\s*\n", vtt or ""):
        block = block.strip()
        if not block or block.upper().startswith("WEBVTT"):
            continue
        # A cue is: [optional id line], "start --> end", then one+ text lines.
        text_lines = [
            ln
            for ln in block.splitlines()
            if "-->" not in ln and not ln.strip().isdigit() and ln.strip()
        ]
        text = " ".join(text_lines).strip()
        if not text:
            continue
        m = _VOICE_RE.search(text)
        if m:
            speaker = m.group(1).strip() or None
            spoken = _TAG_RE.sub("", m.group(2)).strip()
        else:
            speaker = None
            spoken = _TAG_RE.sub("", text).strip()
        if spoken:
            lines.append((speaker, spoken))

    # Merge runs from the same speaker so the transcript reads as turns.
    out: list[str] = []
    last: str | None = None
    for i, (speaker, spoken) in enumerate(lines):
        if i > 0 and speaker == last and out:
            out[-1] = f"{out[-1]} {spoken}"
        else:
            out.append(f"{speaker}: {spoken}" if speaker else spoken)
        last = speaker
    text = "\n".join(out)
    return text[-_TRANSCRIPT_CHARS:]


async def fetch_meeting_transcript(
    external_meeting: dict[str, Any] | None,
) -> tuple[bool, str, str | None]:
    """Return ``(available, transcript_text, detail)`` for the linked meeting.

    Never raises. ``available`` is False (with a human ``detail``) when WorkIQ is
    off, the meeting isn't a Teams online meeting with a join URL, or no
    transcript is published yet.
    """
    if not external_meeting:
        return False, "", "No meeting is linked to this session."
    join_url = external_meeting.get("join_url")
    if not isinstance(join_url, str) or not join_url.strip():
        return (
            False,
            "",
            "The linked meeting has no Teams join link, so its transcript can't be located.",
        )
    join_url = join_url.strip()

    from precursor.backend.services.mcp.client import get_mcp_client_manager

    manager = get_mcp_client_manager()
    try:
        bundle = await manager.acquire([WORKIQ_SERVER])
    except Exception as exc:  # pragma: no cover - defensive
        logger.info("WorkIQ acquire failed: %s", exc)
        return False, "", f"WorkIQ is unavailable: {exc}"

    try:
        if WORKIQ_SERVER not in bundle.workers:
            detail = (
                bundle.unavailable[0][1]
                if bundle.unavailable
                else "WorkIQ (Microsoft 365) is not enabled. Turn it on in Settings → MCP."
            )
            return False, "", detail

        tool_names = {t.name for t in bundle.tools if t.server == WORKIQ_SERVER}
        if "fetch" not in tool_names:
            return False, "", "WorkIQ doesn't expose a fetch tool for Graph."

        async def _fetch(path: str) -> Any:
            result = await bundle.call_tool(WORKIQ_SERVER, "fetch", {"entityUrls": [path]})
            return _result_to_json(result)

        # 1) Resolve the online meeting by its Teams join URL.
        safe = join_url.replace("'", "''")
        meetings = _rows_from(
            await _fetch(f"/me/onlineMeetings?$filter=JoinWebUrl eq '{safe}'&$select=id")
        )
        meeting_id = next(
            (str(m["id"]) for m in meetings if isinstance(m.get("id"), str) and m["id"]),
            None,
        )
        if not meeting_id:
            return (
                False,
                "",
                "Couldn't find this Teams meeting — transcripts are only available to the "
                "meeting organizer, and require OnlineMeetingTranscript.Read.All.",
            )

        # 2) List transcripts, newest first.
        transcripts = _rows_from(
            await _fetch(
                f"/me/onlineMeetings/{meeting_id}/transcripts?$orderby=createdDateTime desc"
            )
        )
        transcript_id = next(
            (str(t["id"]) for t in transcripts if isinstance(t.get("id"), str) and t["id"]),
            None,
        )
        if not transcript_id:
            return (
                False,
                "",
                "No transcript is published for this meeting yet. Teams publishes it a few "
                "minutes after the meeting ends (transcription must have been on).",
            )

        # 3) Fetch the content as WebVTT and parse it.
        content = _content_from(
            await _fetch(
                f"/me/onlineMeetings/{meeting_id}/transcripts/{transcript_id}"
                "/content?$format=text/vtt"
            )
        )
        text = parse_vtt(content)
        if not text.strip():
            return False, "", "The transcript came back empty."
        return True, text, None
    except Exception as exc:  # pragma: no cover - defensive
        logger.info("WorkIQ transcript fetch failed: %s", exc)
        return False, "", f"Couldn't read the Teams transcript: {exc}"
    finally:
        if bundle.ephemeral:
            await bundle.aclose()
