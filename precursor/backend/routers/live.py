"""Live meeting assistant endpoints.

A meeting session records an ongoing meeting: the browser transcribes audio into
segments while the backend derives live insights from a rolling window. This
router owns the session lifecycle, transcript ingestion, live analysis, and a
direct Q&A endpoint. Summary-to-topic lands in the next phase.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from precursor.backend.db import get_session
from precursor.backend.models import (
    MeetingInsight,
    MeetingSegment,
    MeetingSession,
    Message,
    MessageRole,
    Topic,
)
from precursor.backend.schemas import (
    AgendaEvent,
    AgendaResponse,
    AttendeesUpdate,
    LinkMeetingRequest,
    MeetingAskRequest,
    MeetingInsightRead,
    MeetingSegmentCreate,
    MeetingSegmentRead,
    MeetingSessionCreate,
    MeetingSessionRead,
    MeetingSessionUpdate,
    MeetingSummaryPost,
    MeetingSummaryPostResult,
    MeetingSummaryResult,
    SpeakerRenameRequest,
    TopicSummaryResult,
)
from precursor.backend.services.app_settings import (
    resolve_live_fast_model,
    resolve_live_reasoning_effort,
)
from precursor.backend.services.events import publish_meeting_changed, publish_message_changed
from precursor.backend.services.llm import get_llm_provider
from precursor.backend.services.llm.base import ChatMessage, TextDeltaEvent
from precursor.backend.services.meeting_agenda import fetch_agenda
from precursor.backend.services.meeting_analysis import (
    analyze_session,
    display_label,
    language_name,
)
from precursor.backend.services.meeting_summary import (
    generate_summary,
    summarize_topic_conversation,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live", tags=["live"])

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return _SLUG_RE.sub("-", ascii_only.lower()).strip("-")[:80]


async def _allocate_slug(session: AsyncSession, base: str) -> str:
    base = base or "session"
    candidate = base
    n = 2
    while True:
        existing = (
            await session.execute(select(MeetingSession.id).where(MeetingSession.slug == candidate))
        ).first()
        if existing is None:
            return candidate
        candidate = f"{base}-{n}"
        n += 1


async def _get_session_or_404(session_id: int, session: AsyncSession) -> MeetingSession:
    ms = await session.get(MeetingSession, session_id)
    if ms is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Meeting session not found")
    return ms


async def _validate_topic(topic_id: int | None, session: AsyncSession) -> None:
    if topic_id is None:
        return
    if await session.get(Topic, topic_id) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Attached topic not found")


@router.get("", response_model=list[MeetingSessionRead])
async def list_sessions(
    session: AsyncSession = Depends(get_session),
) -> list[MeetingSession]:
    result = await session.execute(
        select(MeetingSession).order_by(MeetingSession.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("", response_model=MeetingSessionRead, status_code=status.HTTP_201_CREATED)
async def create_session_endpoint(
    payload: MeetingSessionCreate,
    session: AsyncSession = Depends(get_session),
) -> MeetingSession:
    await _validate_topic(payload.topic_id, session)

    title = (payload.title or "").strip() or (f"Live session — {datetime.now(UTC):%Y-%m-%d %H:%M}")
    slug = await _allocate_slug(session, _slugify(payload.slug or title))

    ms = MeetingSession(
        title=title,
        slug=slug,
        status="active",
        language=(payload.language or "").strip() or None,
        topic_id=payload.topic_id,
    )
    session.add(ms)
    await session.commit()
    await session.refresh(ms)
    await publish_meeting_changed(ms.id)
    return ms


@router.get("/{session_id}", response_model=MeetingSessionRead)
async def get_session_endpoint(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> MeetingSession:
    return await _get_session_or_404(session_id, session)


@router.patch("/{session_id}", response_model=MeetingSessionRead)
async def update_session_endpoint(
    session_id: int,
    payload: MeetingSessionUpdate,
    session: AsyncSession = Depends(get_session),
) -> MeetingSession:
    ms = await _get_session_or_404(session_id, session)
    data = payload.model_dump(exclude_unset=True)

    if "topic_id" in data:
        await _validate_topic(data["topic_id"], session)
    if "title" in data and data["title"] is not None:
        data["title"] = data["title"].strip() or ms.title
    if "language" in data:
        data["language"] = (data["language"] or "").strip() or None
    # Transitioning to "ended" stamps ended_at once.
    if data.get("status") == "ended" and ms.ended_at is None:
        ms.ended_at = datetime.now(UTC)

    for key, value in data.items():
        setattr(ms, key, value)
    await session.commit()
    await session.refresh(ms)
    await publish_meeting_changed(ms.id)
    return ms


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session_endpoint(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    ms = await _get_session_or_404(session_id, session)
    await session.delete(ms)
    await session.commit()
    await publish_meeting_changed(session_id)


# --------------------------------------------------------------------------
# Transcript segments
# --------------------------------------------------------------------------


@router.get("/{session_id}/segments", response_model=list[MeetingSegmentRead])
async def list_segments(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> list[MeetingSegment]:
    await _get_session_or_404(session_id, session)
    result = await session.execute(
        select(MeetingSegment)
        .where(MeetingSegment.session_id == session_id)
        .order_by(MeetingSegment.created_at, MeetingSegment.id)
    )
    return list(result.scalars().all())


@router.post(
    "/{session_id}/segments",
    response_model=MeetingSegmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def append_segment(
    session_id: int,
    payload: MeetingSegmentCreate,
    session: AsyncSession = Depends(get_session),
) -> MeetingSegment:
    ms = await _get_session_or_404(session_id, session)
    # First persisted phrase marks when recording actually began.
    if ms.started_at is None:
        ms.started_at = datetime.now(UTC)

    segment = MeetingSegment(
        session_id=session_id,
        text=payload.text.strip(),
        speaker_label=(payload.speaker_label or "").strip() or None,
        offset_ms=payload.offset_ms,
    )
    session.add(segment)
    await session.commit()
    await session.refresh(segment)
    return segment


@router.post("/{session_id}/speakers", response_model=MeetingSessionRead)
async def rename_speaker(
    session_id: int,
    payload: SpeakerRenameRequest,
    session: AsyncSession = Depends(get_session),
) -> MeetingSession:
    """Map a raw diarization label to a display name for the whole session.

    Segments keep their raw label; the mapping is applied at display + analysis
    time, so renaming updates every past and future phrase from that speaker.
    An empty name (or one equal to the label) clears the mapping.
    """
    ms = await _get_session_or_404(session_id, session)
    label = payload.label.strip()
    name = payload.name.strip()
    names = ms.speaker_names
    if not name or name == label:
        names.pop(label, None)
    else:
        names[label] = name
    ms.speaker_names_json = json.dumps(names, ensure_ascii=False)
    await session.commit()
    await session.refresh(ms)
    await publish_meeting_changed(ms.id)
    return ms


@router.put("/{session_id}/attendees", response_model=MeetingSessionRead)
async def set_attendees(
    session_id: int,
    payload: AttendeesUpdate,
    session: AsyncSession = Depends(get_session),
) -> MeetingSession:
    """Replace the meeting's attendee list (used in the summary)."""
    ms = await _get_session_or_404(session_id, session)
    # De-dupe while preserving order; drop blanks.
    seen: set[str] = set()
    cleaned: list[str] = []
    for name in payload.attendees:
        n = name.strip()
        if n and n not in seen:
            seen.add(n)
            cleaned.append(n)
    ms.attendees_json = json.dumps(cleaned, ensure_ascii=False)
    await session.commit()
    await session.refresh(ms)
    await publish_meeting_changed(ms.id)
    return ms


# --------------------------------------------------------------------------
# Live analysis + insights + Q&A
# --------------------------------------------------------------------------


@router.get("/{session_id}/insights", response_model=list[MeetingInsightRead])
async def list_insights(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> list[MeetingInsight]:
    await _get_session_or_404(session_id, session)
    result = await session.execute(
        select(MeetingInsight)
        .where(MeetingInsight.session_id == session_id)
        .order_by(MeetingInsight.created_at, MeetingInsight.id)
    )
    return list(result.scalars().all())


@router.post("/{session_id}/analyze", response_model=list[MeetingInsightRead])
async def analyze(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> list[MeetingInsight]:
    """Re-derive the insight snapshot from the rolling transcript window."""
    await _get_session_or_404(session_id, session)
    try:
        return await analyze_session(session, session_id)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Analysis failed: {exc}") from exc


@router.post("/{session_id}/ask")
async def ask(
    session_id: int,
    payload: MeetingAskRequest,
    session: AsyncSession = Depends(get_session),
) -> EventSourceResponse:
    """Answer a direct question using the transcript + attached topic context.

    Streams the answer as SSE ``token`` events, then a ``done`` event. The
    exchange is not persisted (it's a live aide, not a transcript entry).
    """
    ms = await _get_session_or_404(session_id, session)

    segments = list(
        (
            await session.execute(
                select(MeetingSegment)
                .where(MeetingSegment.session_id == session_id)
                .order_by(MeetingSegment.created_at, MeetingSegment.id)
            )
        )
        .scalars()
        .all()
    )
    names = ms.speaker_names
    transcript = "\n".join(f"[{display_label(s.speaker_label, names)}] {s.text}" for s in segments)[
        -6000:
    ]

    topic_context = ""
    if ms.topic_id is not None:
        topic = await session.get(Topic, ms.topic_id)
        if topic is not None:
            rows = (
                (
                    await session.execute(
                        select(Message)
                        .where(Message.topic_id == ms.topic_id)
                        .order_by(Message.created_at.desc())
                        .limit(40)
                    )
                )
                .scalars()
                .all()
            )
            parts = [f"Topic: {topic.title}"]
            if topic.description:
                parts.append(topic.description)
            for msg in reversed(rows):
                if msg.content:
                    parts.append(f"{msg.role.value}: {msg.content}")
            topic_context = "\n".join(parts)[-4000:]

    system = (
        "You are a live meeting assistant. Answer the user's question concisely "
        "using the meeting transcript and any attached topic context below. If "
        "the answer isn't in the material, say so briefly and offer your best "
        "guidance. Prefer a short, direct answer over a long one."
    )
    lang = language_name(ms.language)
    if lang:
        system += f" Respond in {lang}."
    user_parts = [
        f"Question: {payload.question}",
        f"\nTranscript so far:\n{transcript or '(empty)'}",
    ]
    if topic_context:
        user_parts.append(f"\nAttached topic context:\n{topic_context}")

    provider = await get_llm_provider(session)
    model = await resolve_live_fast_model(session)
    effort = await resolve_live_reasoning_effort(session)

    async def event_stream() -> AsyncIterator[dict[str, str]]:
        try:
            async for event in provider.stream_chat_with_tools(
                model=model,
                messages=[
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user", content="\n".join(user_parts)),
                ],
                tools=[],
                reasoning_effort=effort or None,
            ):
                if isinstance(event, TextDeltaEvent) and event.content:
                    yield {"event": "token", "data": json.dumps({"content": event.content})}
            yield {"event": "done", "data": json.dumps({})}
        except Exception as exc:  # surface a clean error frame to the client
            logger.warning("Meeting Q&A stream failed: %s", exc)
            yield {"event": "error", "data": json.dumps({"message": str(exc)})}

    return EventSourceResponse(event_stream())


# --------------------------------------------------------------------------
# Summary + post-to-topic
# --------------------------------------------------------------------------


@router.post("/{session_id}/summary", response_model=MeetingSummaryResult)
async def summarize(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> MeetingSummaryResult:
    """Generate a markdown recap of the session (not persisted)."""
    await _get_session_or_404(session_id, session)
    try:
        text, model = await generate_summary(session, session_id)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Summary failed: {exc}") from exc
    if not text:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Nothing to summarise yet — record some of the meeting first.",
        )
    return MeetingSummaryResult(summary=text, model=model)


@router.post(
    "/{session_id}/summary/post",
    response_model=MeetingSummaryPostResult,
    status_code=status.HTTP_201_CREATED,
)
async def post_summary_to_topic(
    session_id: int,
    payload: MeetingSummaryPost,
    session: AsyncSession = Depends(get_session),
) -> MeetingSummaryPostResult:
    """Append the (possibly edited) summary as a message in the linked topic."""
    ms = await _get_session_or_404(session_id, session)
    if ms.topic_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Attach a topic to this session before posting a summary.",
        )
    if await session.get(Topic, ms.topic_id) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Linked topic no longer exists.")

    body = f"**Meeting summary — {ms.title}**\n\n{payload.summary.strip()}"
    msg = Message(topic_id=ms.topic_id, role=MessageRole.ASSISTANT, content=body)
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    await publish_message_changed(ms.topic_id)
    return MeetingSummaryPostResult(topic_id=ms.topic_id, message_id=msg.id)


# --------------------------------------------------------------------------
# Context: topic summary + M365 agenda (WorkIQ)
# --------------------------------------------------------------------------


@router.post("/{session_id}/topic-summary", response_model=TopicSummaryResult)
async def topic_summary(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> TopicSummaryResult:
    """Summarize the attached topic's conversation as meeting context."""
    ms = await _get_session_or_404(session_id, session)
    if ms.topic_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No topic attached to this session.")
    try:
        text, model = await summarize_topic_conversation(session, ms.topic_id, language=ms.language)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Summary failed: {exc}") from exc
    if not text:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "The topic has no conversation to summarize yet."
        )
    return TopicSummaryResult(summary=text, model=model)


@router.get("/m365/agenda", response_model=AgendaResponse)
async def agenda() -> AgendaResponse:
    """List today's M365 calendar meetings via WorkIQ (fail-closed)."""
    available, events, detail = await fetch_agenda()
    return AgendaResponse(
        available=available,
        events=[AgendaEvent(**e) for e in events],
        detail=detail,
    )


@router.post("/{session_id}/meeting", response_model=MeetingSessionRead)
async def link_meeting(
    session_id: int,
    payload: LinkMeetingRequest,
    session: AsyncSession = Depends(get_session),
) -> MeetingSession:
    """Link an agenda meeting: store it and merge its invitees into attendees."""
    ms = await _get_session_or_404(session_id, session)
    ms.external_meeting_json = payload.model_dump_json()

    merged: list[str] = list(ms.attendees)
    seen = set(merged)
    for att in payload.attendees:
        name = att.name.strip()
        if name and name not in seen:
            seen.add(name)
            merged.append(name)
    ms.attendees_json = json.dumps(merged, ensure_ascii=False)

    await session.commit()
    await session.refresh(ms)
    await publish_meeting_changed(ms.id)
    return ms
