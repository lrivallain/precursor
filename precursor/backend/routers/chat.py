"""Chat router — list messages, post a new turn, stream the assistant reply over SSE."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal, get_session
from precursor.backend.models import Message, MessageRole, Topic
from precursor.backend.schemas import ChatRequest, MessageRead
from precursor.backend.services.github_client import GitHubClient
from precursor.backend.services.llm import get_llm_provider
from precursor.backend.services.llm.base import ChatMessage

router = APIRouter(prefix="/api/topics/{topic_id}/messages", tags=["chat"])


@router.get("", response_model=list[MessageRead])
async def list_messages(
    topic_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[Message]:
    if await session.get(Topic, topic_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    result = await session.execute(
        select(Message).where(Message.topic_id == topic_id).order_by(Message.created_at)
    )
    return list(result.scalars().all())


async def _build_system_context(session: AsyncSession, topic: Topic) -> str:
    """Compose system prompt: topic context + GitHub issue + labels (newest comments first)."""
    parts: list[str] = [
        "You are Precursor, a focused assistant for the topic below. "
        "Use the linked GitHub issue context (when present) as authoritative; "
        "newer updates and comments outweigh older ones.",
        f"Topic title: {topic.title}",
    ]
    if topic.description:
        parts.append(f"Topic description: {topic.description}")

    settings = get_settings()
    repo = topic.github_repo or settings.github_repo
    if repo and topic.github_issue_number and settings.github_token:
        try:
            gh = GitHubClient(token=settings.github_token)
            issue = await gh.get_issue(repo, topic.github_issue_number)
            comments = await gh.list_issue_comments(repo, topic.github_issue_number)
            await gh.aclose()
        except Exception as exc:  # pragma: no cover - network failure is non-fatal
            parts.append(f"(GitHub context unavailable: {exc})")
        else:
            labels = ", ".join(issue.get("labels", [])) or "(none)"
            parts.append(
                f"Linked issue: {repo}#{topic.github_issue_number} — {issue.get('title', '')}"
            )
            parts.append(f"Issue labels: {labels}")
            if issue.get("body"):
                parts.append(f"Issue body:\n{issue['body']}")
            # Newest first, capped to keep prompt size bounded.
            for c in list(reversed(comments))[:10]:
                parts.append(f"Comment by {c['user']} @ {c['updated_at']}:\n{c['body']}")
    return "\n\n".join(parts)


@router.post("/stream")
async def stream_chat(
    topic_id: int,
    payload: ChatRequest,
    session: AsyncSession = Depends(get_session),
) -> EventSourceResponse:
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")

    # Persist the user turn immediately.
    user_msg = Message(topic_id=topic_id, role=MessageRole.USER, content=payload.content)
    session.add(user_msg)
    await session.commit()
    await session.refresh(user_msg)

    # Snapshot history + system context now, before the session is closed.
    system_prompt = await _build_system_context(session, topic)
    history_result = await session.execute(
        select(Message).where(Message.topic_id == topic_id).order_by(Message.created_at)
    )
    history = [
        ChatMessage(role=m.role.value, content=m.content) for m in history_result.scalars().all()
    ]

    model = payload.model or get_settings().llm_model
    provider = get_llm_provider()

    async def event_stream() -> AsyncIterator[dict[str, str]]:
        chunks: list[str] = []
        try:
            yield {
                "event": "user_message",
                "data": json.dumps({"id": user_msg.id, "content": user_msg.content}),
            }
            async for delta in provider.stream_chat(
                model=model,
                messages=[ChatMessage(role="system", content=system_prompt), *history],
            ):
                chunks.append(delta)
                yield {"event": "delta", "data": json.dumps({"content": delta})}

            full = "".join(chunks)
            # Persist assistant turn in a fresh session — the request-scoped one
            # may be closed by the time the generator finishes.
            async with SessionLocal() as write_session:
                assistant = Message(
                    topic_id=topic_id, role=MessageRole.ASSISTANT, content=full
                )
                write_session.add(assistant)
                await write_session.commit()
                await write_session.refresh(assistant)
                yield {
                    "event": "done",
                    "data": json.dumps({"id": assistant.id, "content": full}),
                }
        except Exception as exc:  # surface error to the client stream
            yield {"event": "error", "data": json.dumps({"message": str(exc)})}

    return EventSourceResponse(event_stream())
