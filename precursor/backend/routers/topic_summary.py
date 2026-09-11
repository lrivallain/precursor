"""The editable topic summary — read, edit, refresh and review suggestions.

Distinct from ``routers/summary.py``, which summarises a topic's *linked GitHub
issue*. This one is the brief the user owns: generated on demand from the
conversation, notes and attachments, editable in place, and refreshed through a
per-change review when it has been hand-edited (see
``services/topic_summary.py``).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models import Topic, TopicSummary
from precursor.backend.schemas import (
    SummaryHunkRead,
    SummarySuggestionRead,
    TopicSummaryGenerate,
    TopicSummaryItem,
    TopicSummaryRead,
    TopicSummaryResolve,
    TopicSummarySave,
    TopicSummaryVisibility,
)
from precursor.backend.services import topic_summary as summary_service

router = APIRouter(prefix="/api/topics/{topic_id}/topic-summary", tags=["topic-summary"])
logger = logging.getLogger(__name__)


def _to_read(row: TopicSummary) -> TopicSummaryRead:
    suggestion: SummarySuggestionRead | None = None
    if row.pending_content is not None:
        hunks = summary_service.diff_hunks(row.content, row.pending_content)
        suggestion = SummarySuggestionRead(
            content=row.pending_content,
            model=row.pending_model,
            generated_at=row.pending_generated_at,
            hunks=[SummaryHunkRead(index=h.index, removed=h.removed, added=h.added) for h in hunks],
        )
    return TopicSummaryRead(
        content=row.content,
        visible=row.visible,
        user_edited=row.user_edited,
        model=row.model,
        generated_at=row.generated_at,
        updated_at=row.updated_at,
        suggestion=suggestion,
    )


async def _require_topic(session: AsyncSession, topic_id: int) -> Topic:
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    return topic


@router.get("", response_model=TopicSummaryRead | None)
async def read_summary(
    topic_id: int,
    session: AsyncSession = Depends(get_session),
) -> TopicSummaryRead | None:
    """The topic's summary, or ``null`` when it has never had one."""
    await _require_topic(session, topic_id)
    row = await summary_service.get_summary(session, topic_id)
    return None if row is None else _to_read(row)


@router.put("", response_model=TopicSummaryRead)
async def save_summary(
    topic_id: int,
    payload: TopicSummarySave,
    session: AsyncSession = Depends(get_session),
) -> TopicSummaryRead:
    """Persist a manual edit, marking the summary as user-owned."""
    await _require_topic(session, topic_id)
    row = await summary_service.get_or_create_summary(session, topic_id)
    row.content = summary_service.sanitize_summary(payload.content)
    row.user_edited = True
    if payload.visible is not None:
        row.visible = payload.visible
    # A manual edit supersedes any proposal the user hadn't reviewed.
    row.pending_content = None
    row.pending_model = None
    row.pending_generated_at = None
    await session.commit()
    await session.refresh(row)
    return _to_read(row)


@router.post("/visibility", response_model=TopicSummaryRead)
async def set_visibility(
    topic_id: int,
    payload: TopicSummaryVisibility,
    session: AsyncSession = Depends(get_session),
) -> TopicSummaryRead:
    """Expand / collapse the panel (`/show-summary`, `/hide-summary`)."""
    await _require_topic(session, topic_id)
    row = await summary_service.get_or_create_summary(session, topic_id)
    row.visible = payload.visible
    await session.commit()
    await session.refresh(row)
    return _to_read(row)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_summary(
    topic_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Drop the summary entirely, returning the topic to having none."""
    await _require_topic(session, topic_id)
    row = await summary_service.get_summary(session, topic_id)
    if row is not None:
        await session.delete(row)
        await session.commit()


@router.post("/generate", response_model=TopicSummaryRead)
async def generate_summary(
    topic_id: int,
    payload: TopicSummaryGenerate | None = None,
    session: AsyncSession = Depends(get_session),
) -> TopicSummaryRead:
    """Write a fresh brief from the topic's conversation, notes and files.

    A summary the user never edited is replaced outright. One they did edit is
    left untouched and the proposal is parked for review, so an AI update on
    top of user text is always visible and opt-in.
    """
    topic = await _require_topic(session, topic_id)
    row = await summary_service.get_or_create_summary(session, topic_id)
    review = row.user_edited and bool(row.content.strip())
    try:
        text, model = await summary_service.generate_summary(
            session,
            topic_id=topic_id,
            title=topic.title,
            existing=row.content,
            preserve=review,
            instruction=payload.instruction if payload else None,
        )
    except Exception as exc:  # provider outage, refusal, …
        logger.warning("Topic summary generation failed: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Summary generation failed: {exc}"
        ) from exc

    now = summary_service.utcnow()
    row.visible = True
    if review:
        row.pending_content = text
        row.pending_model = model
        row.pending_generated_at = now
    else:
        row.content = text
        row.model = model
        row.generated_at = now
        row.pending_content = None
        row.pending_model = None
        row.pending_generated_at = None
    await session.commit()
    await session.refresh(row)
    return _to_read(row)


@router.post("/resolve", response_model=TopicSummaryRead)
async def resolve_suggestion(
    topic_id: int,
    payload: TopicSummaryResolve,
    session: AsyncSession = Depends(get_session),
) -> TopicSummaryRead:
    """Merge a pending proposal, keeping only the accepted changes."""
    await _require_topic(session, topic_id)
    row = await summary_service.get_summary(session, topic_id)
    if row is None or row.pending_content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No suggestion to review")
    hunks = summary_service.diff_hunks(row.content, row.pending_content)
    accepted = {index for index in payload.accepted if 0 <= index < len(hunks)}
    merged = summary_service.apply_hunks(row.content, hunks, accepted)
    row.content = merged.strip()
    if accepted:
        row.model = row.pending_model
        row.generated_at = row.pending_generated_at
    row.pending_content = None
    row.pending_model = None
    row.pending_generated_at = None
    await session.commit()
    await session.refresh(row)
    return _to_read(row)


@router.post("/items", response_model=TopicSummaryRead)
async def add_item(
    topic_id: int,
    payload: TopicSummaryItem,
    session: AsyncSession = Depends(get_session),
) -> TopicSummaryRead:
    """Append a pending action or a key fact to the brief."""
    await _require_topic(session, topic_id)
    row = await summary_service.get_or_create_summary(session, topic_id)
    if payload.kind == "todo":
        heading = summary_service.ACTIONS_HEADING
        line = summary_service.format_todo(payload.text)
    else:
        heading = summary_service.INFO_HEADING
        line = summary_service.format_important(payload.text)
    row.content = summary_service.append_item(row.content, heading=heading, item=line).strip()
    row.user_edited = True
    row.visible = True
    await session.commit()
    await session.refresh(row)
    return _to_read(row)
