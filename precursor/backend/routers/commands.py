"""In-chat slash commands.

Each command is a self-contained pair of HTTP endpoints — a `/draft` step
that returns an editable preview, and a `/post` step that performs the
actual side-effect after the user confirms (with `/gh-sync` as the only
fire-and-forget exception).

Adding a new command means adding two routes here, an entry to
`frontend/src/lib/commands.ts`, and a dispatcher branch in `ChatPanel`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import Settings, get_settings
from precursor.backend.db import SessionLocal, get_session
from precursor.backend.models import IssueContextCache, Message, MessageRole, Topic
from precursor.backend.routers.summary import refresh_issue_context
from precursor.backend.schemas import MessageRead
from precursor.backend.services.app_settings import (
    resolve_global_github_repo,
    resolve_issue_associations_enabled,
    resolve_llm_model,
)
from precursor.backend.services.events import (
    publish_message_changed,
    publish_topic_changed,
)
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.github_client import GitHubClient
from precursor.backend.services.llm import get_llm_provider
from precursor.backend.services.llm.base import ChatMessage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/topics/{topic_id}/commands", tags=["commands"])


# ---------------------------------------------------------------------------
# Shared request / response shapes
# ---------------------------------------------------------------------------


class DraftRequest(BaseModel):
    """User-supplied free-form text after the command name."""

    text: str | None = None


class CommentDraftResponse(BaseModel):
    draft: str
    source: str  # "user" | "llm"
    repo: str
    issue_number: int


class CommentPostRequest(BaseModel):
    body: str = Field(min_length=1)


class CommentPostResponse(BaseModel):
    repo: str
    issue_number: int
    comment_url: str | None
    message: MessageRead


async def _require_repo(session: AsyncSession, topic_id: int) -> tuple[Topic, str]:
    if not await resolve_issue_associations_enabled(session):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "GitHub issue associations are disabled. Enable the feature in Settings → GitHub.",
        )
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    repo = topic.github_repo or await resolve_global_github_repo(session)
    if not repo:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No repository configured (set one on the topic or globally).",
        )
    return topic, repo


async def _require_linked_issue(session: AsyncSession, topic_id: int) -> tuple[Topic, str, int]:
    topic, repo = await _require_repo(session, topic_id)
    if topic.github_issue_number is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Topic is not linked to a GitHub issue.",
        )
    return topic, repo, topic.github_issue_number


def _require_token(settings: Settings) -> str:
    token = resolve_github_token(settings)
    if not token:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No GitHub token available. Configure one in Settings, set GITHUB_TOKEN, "
            "or sign in with `gh auth login`.",
        )
    return token


async def _persist_receipt(topic_id: int, body: str) -> MessageRead:
    """Append an assistant-role message; use a fresh session so the write
    commits independently of the request-scoped one."""
    async with SessionLocal() as write_session:
        receipt = Message(
            topic_id=topic_id,
            role=MessageRole.ASSISTANT,
            content=body,
        )
        write_session.add(receipt)
        await write_session.commit()
        # Eager-load `attachments` so MessageRead serialization doesn't trigger
        # a lazy load outside the async greenlet context.
        await write_session.refresh(receipt, attribute_names=["attachments"])
        read = MessageRead.model_validate(receipt, from_attributes=True)
    await publish_message_changed(topic_id)
    return read


async def _invalidate_cache(session: AsyncSession, topic_id: int) -> None:
    cached = await session.get(IssueContextCache, topic_id)
    if cached is not None:
        await session.delete(cached)


async def _recent_history(session: AsyncSession, topic_id: int, limit: int = 20) -> list[Message]:
    result = await session.execute(
        select(Message)
        .where(Message.topic_id == topic_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    rows = list(result.scalars().all())
    rows.reverse()
    return rows


async def _build_transcript(session: AsyncSession, topic_id: int) -> str:
    history = await _recent_history(session, topic_id)
    body = "\n\n".join(
        f"[{m.role.value}] {m.content}" for m in history if m.role != MessageRole.SYSTEM
    )
    return body or "(no prior discussion)"


async def _stream_llm(session: AsyncSession, system: str, user: str, *, label: str) -> str:
    provider = get_llm_provider()
    model = await resolve_llm_model(session)
    chunks: list[str] = []
    try:
        async for delta in provider.stream_chat(
            model=model,
            messages=[
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user),
            ],
        ):
            chunks.append(delta)
    except Exception as exc:
        logger.warning("%s: LLM call failed: %s", label, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LLM call failed: {exc}") from exc
    return "".join(chunks).strip()


# ---------------------------------------------------------------------------
# /gh-update — prompt-driven status comment on the linked issue.
#
# Accepts a free-form instruction in `text` ("close it as resolved", "ask the
# owner for an ETA", …). When MCP transport lands, this route will swap to a
# tool-using loop against the GitHub MCP server; the API shape stays the same.
# ---------------------------------------------------------------------------


@router.post("/gh-update/draft", response_model=CommentDraftResponse)
async def gh_update_draft(
    topic_id: int,
    payload: DraftRequest,
    session: AsyncSession = Depends(get_session),
) -> CommentDraftResponse:
    topic, repo, issue_number = await _require_linked_issue(session, topic_id)
    instruction = (payload.text or "").strip()
    transcript = await _build_transcript(session, topic_id)

    system = (
        "You are Precursor, drafting a comment to post on a GitHub issue. "
        "Follow the user's instruction precisely. Output ONLY the comment body "
        "in GitHub-Flavored Markdown — no preamble, no quoted instruction, no "
        "signature. Be concise, factual, and link-friendly. If the instruction "
        "is empty, default to a short status update grounded in the transcript."
    )
    user_prompt = (
        f"Issue: {repo}#{topic.github_issue_number} — {topic.title}\n\n"
        f"Instruction: {instruction or '(none — write a status update)'}\n\n"
        f"Recent conversation transcript:\n{transcript}"
    )

    draft = await _stream_llm(session, system, user_prompt, label="/gh-update draft")
    return CommentDraftResponse(
        draft=draft,
        source="llm",
        repo=repo,
        issue_number=issue_number,
    )


@router.post("/gh-update/post", response_model=CommentPostResponse)
async def gh_update_post(
    topic_id: int,
    payload: CommentPostRequest,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> CommentPostResponse:
    topic, repo, issue_number = await _require_linked_issue(session, topic_id)
    token = _require_token(settings)

    gh = GitHubClient(token=token)
    try:
        comment = await gh.add_issue_comment(
            repo,
            issue_number,
            payload.body,
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to post comment: {exc}") from exc
    finally:
        await gh.aclose()

    await _invalidate_cache(session, topic_id)
    receipt_body = (
        f"**Posted comment to [{repo}#{topic.github_issue_number}]({comment['url']})**\n\n"
        f"{payload.body}"
    )
    message_read = await _persist_receipt(topic_id, receipt_body)
    await session.commit()

    return CommentPostResponse(
        repo=repo,
        issue_number=issue_number,
        comment_url=comment.get("url"),
        message=message_read,
    )


# ---------------------------------------------------------------------------
# /gh-sync — force-refresh the linked issue's cached context.
# ---------------------------------------------------------------------------


class GhSyncResponse(BaseModel):
    repo: str
    issue_number: int
    issue_state: str
    issue_title: str
    message: MessageRead


@router.post("/gh-sync", response_model=GhSyncResponse)
async def gh_sync(
    topic_id: int,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> GhSyncResponse:
    _, repo, issue_number = await _require_linked_issue(session, topic_id)
    token = _require_token(settings)

    # Drop the stale entry first so a partial failure doesn't leave a
    # mismatched cache behind, then regenerate it from GitHub + the LLM.
    await _invalidate_cache(session, topic_id)
    await session.commit()

    try:
        summary = await refresh_issue_context(
            topic_id=topic_id,
            repo=repo,
            issue_number=issue_number,
            token=token,
            session=session,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Failed to refresh context: {exc}"
        ) from exc

    receipt_body = (
        f"**Synced [{repo}#{summary.issue_number}]({summary.issue_url or ''})** — "
        f"state: `{summary.issue_state}` — title: _{summary.issue_title}_.\n\n"
        f"Cached context regenerated with `{summary.model}`."
    )
    message_read = await _persist_receipt(topic_id, receipt_body)

    return GhSyncResponse(
        repo=repo,
        issue_number=summary.issue_number,
        issue_state=summary.issue_state,
        issue_title=summary.issue_title,
        message=message_read,
    )


# ---------------------------------------------------------------------------
# /gh-create — create a new GitHub issue, link the topic to it.
# ---------------------------------------------------------------------------


class GhCreateDraftResponse(BaseModel):
    title: str
    body: str
    repo: str
    source: str  # "user" | "llm" | "topic"


class GhCreatePostRequest(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    body: str = ""


class GhCreatePostResponse(BaseModel):
    repo: str
    issue_number: int
    issue_url: str | None
    issue_title: str
    message: MessageRead


@router.post("/gh-create/draft", response_model=GhCreateDraftResponse)
async def gh_create_draft(
    topic_id: int,
    payload: DraftRequest,
    session: AsyncSession = Depends(get_session),
) -> GhCreateDraftResponse:
    topic, repo = await _require_repo(session, topic_id)
    if topic.github_issue_number is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Topic is already linked to {repo}#{topic.github_issue_number}.",
        )

    instruction = (payload.text or "").strip()
    transcript = await _build_transcript(session, topic_id)

    system = (
        "You are Precursor, drafting a brand-new GitHub issue from a working "
        "conversation. Respond with EXACTLY two sections separated by a blank "
        "line:\n"
        "TITLE: <a single concise imperative title, max 80 chars, no markdown>\n\n"
        "<body in GitHub-Flavored Markdown: a short context paragraph, then "
        "sections like '### Context', '### Proposal', '### Acceptance "
        "criteria' as relevant. No preamble, no signature.>"
    )
    user_prompt = (
        f"Repository: {repo}\n"
        f"Topic title: {topic.title}\n"
        f"Topic description: {topic.description or '(none)'}\n\n"
        f"Instruction: {instruction or '(none — derive from the transcript)'}\n\n"
        f"Recent conversation transcript:\n{transcript}"
    )

    raw = await _stream_llm(session, system, user_prompt, label="/gh-create draft")
    title, body = _split_title_body(raw, fallback_title=topic.title)
    return GhCreateDraftResponse(title=title, body=body, repo=repo, source="llm")


def _split_title_body(raw: str, *, fallback_title: str) -> tuple[str, str]:
    """Parse the `TITLE: ...\n\n<body>` shape; tolerate slight deviations."""
    text = raw.strip()
    if not text:
        return fallback_title, ""
    lines = text.splitlines()
    first = lines[0].strip()
    if first.lower().startswith("title:"):
        title = first[len("title:") :].strip().strip("`\"' ")
        body = "\n".join(lines[1:]).lstrip("\n").strip()
        return title or fallback_title, body
    # Fallback: assume the first line is the title.
    return first[:200] or fallback_title, "\n".join(lines[1:]).strip()


@router.post("/gh-create/post", response_model=GhCreatePostResponse)
async def gh_create_post(
    topic_id: int,
    payload: GhCreatePostRequest,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> GhCreatePostResponse:
    topic, repo = await _require_repo(session, topic_id)
    if topic.github_issue_number is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Topic is already linked to {repo}#{topic.github_issue_number}.",
        )
    token = _require_token(settings)

    gh = GitHubClient(token=token)
    try:
        issue = await gh.create_issue(repo, title=payload.title.strip(), body=payload.body or None)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to create issue: {exc}") from exc
    finally:
        await gh.aclose()

    # Link the topic to the freshly created issue.
    topic.github_repo = repo
    topic.github_issue_number = issue["number"]
    await _invalidate_cache(session, topic_id)
    await session.commit()
    await publish_topic_changed(topic_id)

    receipt_body = (
        f"**Created issue [{repo}#{issue['number']}]({issue['url']}) — "
        f"_{issue['title']}_** and linked it to this topic."
    )
    message_read = await _persist_receipt(topic_id, receipt_body)

    return GhCreatePostResponse(
        repo=repo,
        issue_number=issue["number"],
        issue_url=issue.get("url"),
        issue_title=issue["title"],
        message=message_read,
    )


# ---------------------------------------------------------------------------
# /gh-close — optionally comment, then close the linked issue.
# ---------------------------------------------------------------------------


class GhClosePostRequest(BaseModel):
    body: str = ""  # optional closing comment
    state_reason: str = Field(default="completed", pattern="^(completed|not_planned|reopened)$")


class GhCloseResponse(BaseModel):
    repo: str
    issue_number: int
    issue_state: str
    comment_url: str | None
    message: MessageRead


@router.post("/gh-close/draft", response_model=CommentDraftResponse)
async def gh_close_draft(
    topic_id: int,
    payload: DraftRequest,
    session: AsyncSession = Depends(get_session),
) -> CommentDraftResponse:
    topic, repo, issue_number = await _require_linked_issue(session, topic_id)
    instruction = (payload.text or "").strip()
    transcript = await _build_transcript(session, topic_id)

    system = (
        "You are Precursor, drafting the FINAL closing comment for a GitHub "
        "issue. Output ONLY the comment body in GitHub-Flavored Markdown — no "
        "preamble, no signature. Be concise. Lead with a one-line resolution "
        "summary, then a short bullet list of: what was done, decisions made, "
        "any follow-up issues to file. Mark the issue as resolved."
    )
    user_prompt = (
        f"Issue: {repo}#{topic.github_issue_number} — {topic.title}\n\n"
        f"Instruction: {instruction or '(none — summarise how it was resolved)'}\n\n"
        f"Recent conversation transcript:\n{transcript}"
    )

    draft = await _stream_llm(session, system, user_prompt, label="/gh-close draft")
    return CommentDraftResponse(
        draft=draft,
        source="llm",
        repo=repo,
        issue_number=issue_number,
    )


@router.post("/gh-close/post", response_model=GhCloseResponse)
async def gh_close_post(
    topic_id: int,
    payload: GhClosePostRequest,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> GhCloseResponse:
    _, repo, issue_number = await _require_linked_issue(session, topic_id)
    token = _require_token(settings)
    body = payload.body.strip()

    gh = GitHubClient(token=token)
    comment_url: str | None = None
    try:
        if body:
            comment = await gh.add_issue_comment(
                repo,
                issue_number,
                body,
            )
            comment_url = comment.get("url")
        target_state = "open" if payload.state_reason == "reopened" else "closed"
        # GitHub only accepts state_reason values matching the new state.
        state_reason = None if target_state == "open" else payload.state_reason
        updated = await gh.update_issue(
            repo,
            issue_number,
            state=target_state,
            state_reason=state_reason,
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to close issue: {exc}") from exc
    finally:
        await gh.aclose()

    await _invalidate_cache(session, topic_id)
    await session.commit()

    parts = [
        f"**Closed [{repo}#{updated['number']}]({updated.get('url') or ''})** "
        f"as `{payload.state_reason}` (state: `{updated['state']}`).",
    ]
    if body:
        link = f"[comment]({comment_url})" if comment_url else "comment"
        parts.append(f"Posted closing {link}:\n\n{body}")
    receipt_body = "\n\n".join(parts)
    message_read = await _persist_receipt(topic_id, receipt_body)

    return GhCloseResponse(
        repo=repo,
        issue_number=updated["number"],
        issue_state=updated["state"],
        comment_url=comment_url,
        message=message_read,
    )


# ---------------------------------------------------------------------------
# /notes — capture freeform notes (e.g. during a meeting) and decide later
# what to do with them: rephrase via LLM, post as an issue comment, append
# to the discussion, or send as a prompt for AI suggestions.
#
# Routes here cover only the bits the LLM or DB layer needs to own:
#   - /notes/rephrase: clean up / structure the raw text.
#   - /notes/append:   persist the text verbatim as a user message.
# "Post as comment" reuses /gh-update/post. "Append + ask AI" reuses the
# regular streaming endpoint on the frontend.
# ---------------------------------------------------------------------------


class NotesRephraseRequest(BaseModel):
    text: str = Field(min_length=1)
    instruction: str | None = None


class NotesRephraseResponse(BaseModel):
    text: str


class NotesAppendRequest(BaseModel):
    text: str = Field(min_length=1)


class NotesAppendResponse(BaseModel):
    message: MessageRead


@router.post("/notes/rephrase", response_model=NotesRephraseResponse)
async def notes_rephrase(
    topic_id: int,
    payload: NotesRephraseRequest,
    session: AsyncSession = Depends(get_session),
) -> NotesRephraseResponse:
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")

    instruction = (payload.instruction or "").strip()
    system = (
        "You clean up rough meeting / working notes. Preserve every fact and "
        "decision verbatim; do not invent content. Reorganise into short, "
        "scannable bullet points grouped by theme when useful, fix typos and "
        "obvious shorthand, and keep neutral phrasing. Output ONLY the cleaned "
        "notes in GitHub-Flavored Markdown — no preamble, no signature."
    )
    user_prompt = (
        f"Topic: {topic.title}\n\n"
        f"Extra instruction: {instruction or '(none — default cleanup)'}\n\n"
        f"Raw notes:\n{payload.text}"
    )
    rebuilt = await _stream_llm(session, system, user_prompt, label="/notes rephrase")
    return NotesRephraseResponse(text=rebuilt or payload.text)


@router.post("/notes/append", response_model=NotesAppendResponse)
async def notes_append(
    topic_id: int,
    payload: NotesAppendRequest,
    session: AsyncSession = Depends(get_session),
) -> NotesAppendResponse:
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")

    body = f"**Notes**\n\n{payload.text.strip()}"
    async with SessionLocal() as write_session:
        msg = Message(
            topic_id=topic_id,
            role=MessageRole.USER,
            content=body,
        )
        write_session.add(msg)
        await write_session.commit()
        await write_session.refresh(msg, attribute_names=["attachments"])
        message_read = MessageRead.model_validate(msg, from_attributes=True)
    await publish_message_changed(topic_id)
    return NotesAppendResponse(message=message_read)
