"""Fetch the user's upcoming M365 agenda via the WorkIQ MCP server.

The Live "Context" section lets the user link a calendar meeting so its invitees
seed the summary's attendees. There's no direct Graph integration in Precursor;
instead we call the built-in ``workiq`` MCP server (Microsoft 365) programmatically
through the MCP client manager. Best-effort and fail-closed: if WorkIQ isn't
configured/authenticated, we report ``available=False`` with a detail message so
the UI degrades gracefully.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from precursor.backend.services.mcp.client import get_mcp_client_manager

logger = logging.getLogger(__name__)

WORKIQ_SERVER = "workiq"


def _result_to_json(payload: Any) -> Any:
    """Coerce an MCP tool result (text blocks or structuredContent) to JSON."""
    structured = getattr(payload, "structuredContent", None)
    if isinstance(structured, (dict, list)):
        return structured
    content = getattr(payload, "content", None)
    if content:
        texts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                texts.append(text)
        joined = "\n".join(texts).strip()
        if joined:
            try:
                return json.loads(joined)
            except json.JSONDecodeError:
                return None
    return None


def _events_from(data: Any) -> list[dict[str, Any]]:
    """Extract a list of raw Graph event dicts from a variety of shapes."""
    if isinstance(data, dict):
        for key in ("value", "events", "items", "data"):
            v = data.get(key)
            if isinstance(v, list):
                return [e for e in v if isinstance(e, dict)]
        # A single event object.
        if "subject" in data:
            return [data]
        return []
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    return []


def _person_name(entry: Any) -> tuple[str, str | None]:
    """Return (name, email) from a Graph recipient/attendee shape."""
    if not isinstance(entry, dict):
        return (str(entry), None)
    email = entry.get("emailAddress") if isinstance(entry.get("emailAddress"), dict) else None
    if email:
        return (str(email.get("name") or email.get("address") or "").strip(), email.get("address"))
    name = entry.get("name") or entry.get("displayName") or ""
    return (str(name).strip(), entry.get("address") or entry.get("email"))


def _normalize_event(raw: dict[str, Any]) -> dict[str, Any]:
    start = raw.get("start")
    end = raw.get("end")
    organizer_name, _ = _person_name(raw.get("organizer"))
    attendees: list[dict[str, Any]] = []
    for a in raw.get("attendees") or []:
        name, email = _person_name(a)
        if name:
            attendees.append({"name": name, "email": email})
    return {
        "id": raw.get("id"),
        "subject": str(raw.get("subject") or "(no subject)"),
        "start": start.get("dateTime") if isinstance(start, dict) else start,
        "end": end.get("dateTime") if isinstance(end, dict) else end,
        "organizer": organizer_name or None,
        "attendees": attendees,
        "is_online": bool(raw.get("isOnlineMeeting")),
    }


async def fetch_agenda(days: int = 7) -> tuple[bool, list[dict[str, Any]], str | None]:
    """Return (available, events, detail). Never raises."""
    manager = get_mcp_client_manager()
    try:
        bundle = await manager.acquire([WORKIQ_SERVER])
    except Exception as exc:  # pragma: no cover - defensive
        logger.info("WorkIQ acquire failed: %s", exc)
        return False, [], f"WorkIQ is unavailable: {exc}"

    try:
        if WORKIQ_SERVER not in bundle.workers:
            detail = (
                bundle.unavailable[0][1]
                if bundle.unavailable
                else "WorkIQ (Microsoft 365) is not enabled. Turn it on in Settings → MCP."
            )
            return False, [], detail

        tool_names = {t.name for t in bundle.tools if t.server == WORKIQ_SERVER}
        now = datetime.now(UTC)
        end = now + timedelta(days=max(1, days))
        path = (
            f"/me/calendarView(startDateTime='{now:%Y-%m-%dT%H:%M:%SZ}',"
            f"endDateTime='{end:%Y-%m-%dT%H:%M:%SZ}')"
            "?$select=subject,start,end,organizer,attendees,isOnlineMeeting"
            "&$orderby=start/dateTime&$top=25"
        )
        try:
            if "call_function" in tool_names:
                result = await bundle.call_tool(
                    WORKIQ_SERVER, "call_function", {"functionUrl": path}
                )
            elif "fetch" in tool_names:
                result = await bundle.call_tool(WORKIQ_SERVER, "fetch", {"entityUrls": [path]})
            else:
                return False, [], "WorkIQ doesn't expose a calendar tool."
        except Exception as exc:
            logger.info("WorkIQ agenda call failed: %s", exc)
            return False, [], f"Couldn't read the agenda: {exc}"

        data = _result_to_json(result)
        events = [_normalize_event(e) for e in _events_from(data)]
        return True, events, None
    finally:
        if bundle.ephemeral:
            await bundle.aclose()
