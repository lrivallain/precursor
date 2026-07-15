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
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from precursor.backend.db import get_session
from precursor.backend.models import (
    Attachment,
    Chat,
    MeetingAttachment,
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
    ChatRead,
    ContextNoteAdd,
    ContextNotesUpdate,
    LinkMeetingRequest,
    MeetingAnalyzeResult,
    MeetingAskRequest,
    MeetingAttachmentRead,
    MeetingInsightRead,
    MeetingPostResult,
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
    TranslateRequest,
    TranslateResult,
)
from precursor.backend.services.app_settings import (
    resolve_live_fast_model,
    resolve_live_reasoning_effort,
)
from precursor.backend.services.blob_store import blob_path, write_blob
from precursor.backend.services.events import publish_meeting_changed, publish_message_changed
from precursor.backend.services.image_uploads import read_validated_attachment
from precursor.backend.services.llm import get_llm_provider
from precursor.backend.services.llm.base import ChatMessage, TextDeltaEvent
from precursor.backend.services.meeting_agenda import fetch_agenda
from precursor.backend.services.meeting_analysis import (
    analyze_session,
    context_notes_text,
    display_label,
    language_name,
    meeting_context_text,
    meeting_details_markdown,
    translate_lines,
    translate_transcript,
)
from precursor.backend.services.meeting_summary import (
    generate_summary,
    summarize_topic_conversation,
)
from precursor.backend.services.slugs import allocate_unique_slug, slugify

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live", tags=["live"])

# Optional Live features a session can enable (gates tabs + background work).
VALID_FEATURES = frozenset({"insights", "notes", "assistant", "proactive", "translation"})


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
    slug = await _allocate_slug(session, slugify(payload.slug or title))

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
        # Attaching a different topic invalidates the cached context summary so
        # the Context tab regenerates it for the new topic.
        if data["topic_id"] != ms.topic_id:
            ms.topic_summary = None
    if "title" in data and data["title"] is not None:
        data["title"] = data["title"].strip() or ms.title
    if "language" in data:
        data["language"] = (data["language"] or "").strip() or None
    if "notes" in data and data["notes"] is None:
        data["notes"] = ""
    if "features" in data:
        feats = data.pop("features") or []
        cleaned = [f for f in dict.fromkeys(feats) if f in VALID_FEATURES]
        ms.features_json = json.dumps(cleaned, ensure_ascii=False)
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
        # A named speaker is confirmed present, so seed the summary's attendee
        # list with them (invitees who never speak stay suggestions only).
        attendees = ms.attendees
        if name not in attendees:
            attendees.append(name)
            ms.attendees_json = json.dumps(attendees, ensure_ascii=False)
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


@router.post("/{session_id}/context-notes", response_model=MeetingSessionRead)
async def add_context_note(
    session_id: int,
    payload: ContextNoteAdd,
    session: AsyncSession = Depends(get_session),
) -> MeetingSession:
    """Pin a free-form note to the session's grounding context (e.g. a Q&A answer)."""
    ms = await _get_session_or_404(session_id, session)
    text = payload.text.strip()
    notes = ms.context_notes
    if text and text not in notes:
        notes.append(text)
        ms.context_notes_json = json.dumps(notes, ensure_ascii=False)
        await session.commit()
        await session.refresh(ms)
        await publish_meeting_changed(ms.id)
    return ms


@router.put("/{session_id}/context-notes", response_model=MeetingSessionRead)
async def set_context_notes(
    session_id: int,
    payload: ContextNotesUpdate,
    session: AsyncSession = Depends(get_session),
) -> MeetingSession:
    """Replace the session's context notes (used to remove/edit pinned notes)."""
    ms = await _get_session_or_404(session_id, session)
    seen: set[str] = set()
    cleaned: list[str] = []
    for note in payload.notes:
        n = note.strip()
        if n and n not in seen:
            seen.add(n)
            cleaned.append(n)
    ms.context_notes_json = json.dumps(cleaned, ensure_ascii=False)
    await session.commit()
    await session.refresh(ms)
    await publish_meeting_changed(ms.id)
    return ms


def _attachment_read(att: MeetingAttachment) -> MeetingAttachmentRead:
    return MeetingAttachmentRead(
        id=att.id,
        mime=att.mime,
        original_filename=att.original_filename,
        url=f"/api/live/attachments/{att.id}",
        is_image=att.mime.startswith("image/"),
    )


@router.post(
    "/{session_id}/attachments",
    response_model=MeetingAttachmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_meeting_attachment(
    session_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> MeetingAttachmentRead:
    """Store a file pasted/dropped into the live notes and return its serve URL."""
    ms = await _get_session_or_404(session_id, session)
    mime, data = await read_validated_attachment(file)
    att = MeetingAttachment(
        session_id=ms.id,
        mime=mime,
        size=len(data),
        original_filename=(file.filename or "")[:255],
        sha256=write_blob(data),
    )
    session.add(att)
    await session.commit()
    await session.refresh(att)
    return _attachment_read(att)


# Static two-segment path so it doesn't collide with the dynamic /{session_id}.
@router.get("/attachments/{attachment_id}")
async def get_meeting_attachment(
    attachment_id: int, session: AsyncSession = Depends(get_session)
) -> FileResponse:
    att = await session.get(MeetingAttachment, attachment_id)
    if att is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    path = blob_path(att.sha256)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment content missing")
    return FileResponse(
        path, media_type=att.mime, headers={"Cache-Control": "private, max-age=3600"}
    )


@router.post("/{session_id}/chat", response_model=ChatRead)
async def ensure_chat(session_id: int, session: AsyncSession = Depends(get_session)) -> Chat:
    """Get (or spawn on first ask) the chat attached to this live session.

    The chat is a standard chat — tools, attachments, history, roles — but its
    system context is augmented with the live meeting grounding (see
    ``live_chat_grounding``) on every turn.
    """
    ms = await _get_session_or_404(session_id, session)
    if ms.chat_id is not None:
        existing = await session.get(Chat, ms.chat_id)
        if existing is not None:
            return existing

    slug = await allocate_unique_slug(session, slugify(ms.title) or "live-chat", Chat)
    chat = Chat(title=f"{ms.title} — Live chat", slug=slug)
    session.add(chat)
    await session.commit()
    await session.refresh(chat)

    ms.chat_id = chat.id
    await session.commit()
    await publish_meeting_changed(ms.id)
    return chat


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


@router.post("/{session_id}/analyze", response_model=MeetingAnalyzeResult)
async def analyze(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> MeetingAnalyzeResult:
    """Unified pass: re-derive the insight snapshot from the rolling transcript
    window and decide on a proactive suggestion (empty when none is warranted)."""
    await _get_session_or_404(session_id, session)
    try:
        rows, suggestion = await analyze_session(session, session_id)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Analysis failed: {exc}") from exc
    return MeetingAnalyzeResult(
        insights=[MeetingInsightRead.model_validate(r) for r in rows],
        suggestion=suggestion,
    )


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
    meeting_ctx = meeting_context_text(ms.external_meeting)
    if meeting_ctx:
        user_parts.append(f"\nLinked meeting context:\n{meeting_ctx}")
    if topic_context:
        user_parts.append(f"\nAttached topic context:\n{topic_context}")
    notes_ctx = context_notes_text(ms.context_notes)
    if notes_ctx:
        user_parts.append(f"\nPinned context notes:\n{notes_ctx}")

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


@router.post("/{session_id}/translate", response_model=TranslateResult)
async def translate(
    session_id: int,
    payload: TranslateRequest,
    session: AsyncSession = Depends(get_session),
) -> TranslateResult:
    """Translate the transcript into the requested language. With ``texts`` it
    translates those lines (live, incremental); otherwise the whole transcript."""
    await _get_session_or_404(session_id, session)
    try:
        if payload.texts is not None:
            lines, model = await translate_lines(
                session, session_id, payload.target_lang, payload.texts
            )
            return TranslateResult(
                text="\n".join(lines),
                lines=lines,
                target_lang=payload.target_lang,
                model=model,
            )
        text, model = await translate_transcript(session, session_id, payload.target_lang)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Translation failed: {exc}") from exc
    if not text:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Nothing to translate yet — record some of the meeting first.",
        )
    return TranslateResult(text=text, lines=[text], target_lang=payload.target_lang, model=model)


# --------------------------------------------------------------------------
# Summary + post-to-topic
# --------------------------------------------------------------------------


@router.post("/{session_id}/summary", response_model=MeetingSummaryResult)
async def summarize(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> MeetingSummaryResult:
    """Generate a markdown recap of the session and persist it on the session.

    Stored so a reopened session shows the recap without regenerating; a new
    generation only happens on the user's explicit request.
    """
    ms = await _get_session_or_404(session_id, session)
    try:
        text, model = await generate_summary(session, session_id)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Summary failed: {exc}") from exc
    if not text:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Nothing to summarise yet — record some of the meeting first.",
        )
    ms.summary = text
    await session.commit()
    await publish_meeting_changed(ms.id)
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

    # Copy the note attachments into the topic so they survive the meeting
    # session and render in the posted message's gallery.
    attachments = list(
        (
            await session.execute(
                select(MeetingAttachment)
                .where(MeetingAttachment.session_id == ms.id)
                .order_by(MeetingAttachment.created_at, MeetingAttachment.id)
            )
        )
        .scalars()
        .all()
    )

    summary = payload.summary.strip()
    if attachments:
        # The files move to the message gallery, so strip their raw live-URL
        # references (and any now-empty "Attachments" heading) from the body.
        summary = re.sub(r"^#+\s*Attachments\s*$", "", summary, flags=re.MULTILINE)
        summary = re.sub(r"!?\[[^\]]*\]\(/api/live/attachments/\d+\)", "", summary)
        summary = re.sub(r"^\s*[-*]\s*$", "", summary, flags=re.MULTILINE)
        summary = re.sub(r"\n{3,}", "\n\n", summary).strip()

    body = f"**Meeting summary — {ms.title}**\n\n{summary}"
    msg = Message(topic_id=ms.topic_id, role=MessageRole.ASSISTANT, content=body)
    session.add(msg)
    await session.commit()
    await session.refresh(msg)

    for att in attachments:
        session.add(
            Attachment(
                topic_id=ms.topic_id,
                message_id=msg.id,
                mime=att.mime,
                size=att.size,
                original_filename=att.original_filename,
                sha256=att.sha256,
            )
        )
    if attachments:
        await session.commit()

    # Persist the posted recap and stamp when/where it landed so a reopened
    # session shows the recap and that it already reached the topic. Store the
    # user's full text (not the attachment-stripped body) to match the tab.
    posted_at = datetime.now(UTC)
    ms.summary = payload.summary.strip()
    ms.summary_posted_at = posted_at
    ms.summary_posted_topic_id = ms.topic_id
    await session.commit()

    await publish_message_changed(ms.topic_id)
    await publish_meeting_changed(ms.id)
    return MeetingSummaryPostResult(topic_id=ms.topic_id, message_id=msg.id, posted_at=posted_at)


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
    # Persist the summary so we serve it from cache on later opens instead of
    # re-summarizing (and re-spending tokens) on every display.
    ms.topic_summary = text or None
    await session.commit()
    await publish_meeting_changed(ms.id)
    # An empty topic (no messages yet) is a normal state, not an error: return an
    # empty summary so the UI shows its "nothing to summarize yet" empty state.
    return TopicSummaryResult(summary=text, model=model)


@router.get("/m365/agenda", response_model=AgendaResponse)
async def agenda(start: str | None = None, end: str | None = None) -> AgendaResponse:
    """List the user's M365 meetings for the given window via WorkIQ (fail-closed).

    ``start``/``end`` are ISO-8601 UTC bounds computed by the client for the
    user's local day; defaults to the UTC day when omitted.
    """
    available, events, detail = await fetch_agenda(start, end)
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
    """Link an agenda meeting as grounding context. Invitees are NOT auto-added
    to attendees — only speakers confirmed in the transcript seed that list;
    invitees stay suggestions in the summary."""
    ms = await _get_session_or_404(session_id, session)
    ms.external_meeting_json = payload.model_dump_json()
    await session.commit()
    await session.refresh(ms)
    await publish_meeting_changed(ms.id)
    return ms


@router.post(
    "/{session_id}/meeting/post",
    response_model=MeetingPostResult,
    status_code=status.HTTP_201_CREATED,
)
async def post_meeting_to_topic(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> MeetingPostResult:
    """Post the linked meeting's details as a message in the attached topic."""
    ms = await _get_session_or_404(session_id, session)
    if ms.topic_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Attach a topic to this session before posting."
        )
    if await session.get(Topic, ms.topic_id) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Linked topic no longer exists.")
    if ms.external_meeting is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No meeting is linked to this session.")

    body = meeting_details_markdown(ms.external_meeting)
    msg = Message(topic_id=ms.topic_id, role=MessageRole.ASSISTANT, content=body)
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    await publish_message_changed(ms.topic_id)
    return MeetingPostResult(topic_id=ms.topic_id, message_id=msg.id)


@router.delete("/{session_id}/meeting", response_model=MeetingSessionRead)
async def unlink_meeting(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> MeetingSession:
    """Detach the linked meeting (attendees are kept — edit them if needed)."""
    ms = await _get_session_or_404(session_id, session)
    ms.external_meeting_json = None
    await session.commit()
    await session.refresh(ms)
    await publish_meeting_changed(ms.id)
    return ms
