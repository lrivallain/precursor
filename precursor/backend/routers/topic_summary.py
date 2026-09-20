"""The editable topic summary — read, edit, refresh and review suggestions.

Distinct from ``routers/summary.py``, which summarises a topic's *linked GitHub
issue*. This one is the brief the user owns: generated on demand from the
conversation, notes and attachments, editable in place, and refreshed through a
per-change review when it has been hand-edited (see
``services/topic_summary.py``).
"""

from __future__ import annotations

import logging
from typing import Any

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
        revision=summary_service.revision(row),
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


def _conflict() -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        "The summary changed. Reload it and review your changes before retrying.",
    )


def _check_revision(row: TopicSummary | None, revision: str | None) -> None:
    if revision is not None and (row is None or revision != summary_service.revision(row)):
        raise _conflict()


async def _write(
    session: AsyncSession,
    topic_id: int,
    expected: dict[str, Any] | None,
    **changes: Any,
) -> TopicSummaryRead:
    try:
        return _to_read(await summary_service.write_summary(session, topic_id, expected, changes))
    except summary_service.SummaryConflict as exc:
        raise _conflict() from exc


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
    row = await summary_service.get_summary(session, topic_id)
    _check_revision(row, payload.revision)
    return await _write(
        session,
        topic_id,
        summary_service.snapshot(row),
        content=payload.content,
        user_edited=True,
        **({"visible": payload.visible} if payload.visible is not None else {}),
        **summary_service.CLEAR_SUGGESTION,
    )


@router.post("/visibility", response_model=TopicSummaryRead | None)
async def set_visibility(
    topic_id: int,
    payload: TopicSummaryVisibility,
    session: AsyncSession = Depends(get_session),
) -> TopicSummaryRead | None:
    """Expand / collapse the panel (`/show-summary`, `/hide-summary`).

    Hiding a topic that has no brief is a no-op: it must not conjure an empty
    summary the user then has to delete.
    """
    await _require_topic(session, topic_id)
    row = await summary_service.get_summary(session, topic_id)
    if row is None and not payload.visible:
        return None
    return await _write(session, topic_id, summary_service.snapshot(row), visible=payload.visible)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_summary(
    topic_id: int,
    session: AsyncSession = Depends(get_session),
    revision: str | None = None,
) -> None:
    """Drop the summary entirely, returning the topic to having none."""
    await _require_topic(session, topic_id)
    row = await summary_service.get_summary(session, topic_id)
    _check_revision(row, revision)
    if row is not None:
        try:
            await summary_service.delete_summary(session, row)
        except summary_service.SummaryConflict as exc:
            raise _conflict() from exc


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
    row = await summary_service.get_summary(session, topic_id)
    expected = summary_service.snapshot(row)
    existing = row.content if row else ""
    review = row.user_edited if row else False
    try:
        text, model = await summary_service.generate_summary(
            session,
            topic_id=topic_id,
            title=topic.title,
            existing=existing,
            preserve=review,
            instruction=payload.instruction if payload else None,
        )
    except Exception as exc:  # provider outage, refusal, …
        logger.warning("Topic summary generation failed: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Summary generation failed, please try again.",
        ) from exc

    # The topic may have been deleted while the provider was running.
    if await session.get(Topic, topic_id, populate_existing=True) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    now = summary_service.utcnow()
    changes: dict[str, Any] = {"visible": True, **summary_service.CLEAR_SUGGESTION}
    if review and text != existing:
        changes.update(pending_content=text, pending_model=model, pending_generated_at=now)
    elif not review:
        changes.update(content=text, model=model, generated_at=now)
    return await _write(session, topic_id, expected, **changes)


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
    _check_revision(row, payload.revision)
    hunks = summary_service.diff_hunks(row.content, row.pending_content)
    accepted = set(payload.accepted)
    if not accepted <= {h.index for h in hunks}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown change index")
    merged = summary_service.apply_hunks(row.content, hunks, accepted)
    return await _write(
        session,
        topic_id,
        summary_service.snapshot(row),
        content=merged,
        **(
            {"model": row.pending_model, "generated_at": row.pending_generated_at}
            if accepted
            else {}
        ),
        **summary_service.CLEAR_SUGGESTION,
    )


@router.post("/items", response_model=TopicSummaryRead)
async def add_item(
    topic_id: int,
    payload: TopicSummaryItem,
    session: AsyncSession = Depends(get_session),
) -> TopicSummaryRead:
    """Append a pending action or a key fact to the brief."""
    await _require_topic(session, topic_id)
    row = await summary_service.get_summary(session, topic_id)
    if not payload.text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "An item cannot be blank")
    if payload.kind == "todo":
        heading = summary_service.ACTIONS_HEADING
        line = summary_service.format_todo(payload.text)
    else:
        heading = summary_service.INFO_HEADING
        line = summary_service.format_important(payload.text)
    return await _write(
        session,
        topic_id,
        summary_service.snapshot(row),
        content=summary_service.append_item(row.content if row else "", heading=heading, item=line),
        user_edited=True,
        visible=True,
        **summary_service.CLEAR_SUGGESTION,
    )
