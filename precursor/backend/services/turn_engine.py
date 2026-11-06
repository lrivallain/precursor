"""The conversational turn engine — system context, history hydration, and the
MCP tool loop — shared by the SSE chat routers and the background scheduler.

The engine is container-agnostic: a turn belongs to exactly one of a topic or a
chat (see :data:`ContainerKind`). :func:`run_tool_loop` drives the provider +
MCP tool loop and yields semantic :class:`TurnEvent`s; each consumer applies its
own persistence/emission policy on top. :func:`run_message_stream` is the SSE
consumer (rich persistence + Server-Sent Events); the scheduler
(``services/turn.py``) is the plain consumer. Sharing the loop keeps the two
paths from diverging.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from precursor.backend.db import SessionLocal
from precursor.backend.models import MESSAGE_KIND_COMPACTION, Chat, Message, MessageRole, Topic
from precursor.backend.models.message import (
    MESSAGE_KIND_STEP_AUTOMATION,
    MESSAGE_KIND_STEP_USER_REQUEST,
)
from precursor.backend.models.tool_approval import APPROVAL_KIND_TOOL
from precursor.backend.services import approval_rules as approval_rules_service
from precursor.backend.services import approvals as approval_service
from precursor.backend.services import memories as memory_service
from precursor.backend.services import side_chats, step_plans
from precursor.backend.services import skills as skills_service
from precursor.backend.services.app_settings import (
    DEFAULT_LLM_MAX_ATTACHMENT_CHARS,
    resolve_mcp_expose,
)
from precursor.backend.services.artifacts import artifacts_prompt, record_written_file
from precursor.backend.services.attachment_extraction import (
    attachments_to_image_urls,
    attachments_to_text_context,
    is_image_attachment,
)
from precursor.backend.services.collections import resolve_topic_github_repo
from precursor.backend.services.context_budget import strip_inline_binaries, trim_messages
from precursor.backend.services.events import (
    publish_approval_changed,
    publish_message_changed,
    publish_message_changed_chat,
    publish_stream_ended,
    publish_stream_ended_chat,
    publish_stream_started,
    publish_stream_started_chat,
)
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.github_client import GitHubClient
from precursor.backend.services.llm.base import (
    ChatMessage,
    LLMError,
    ReasoningDeltaEvent,
    TextDeltaEvent,
    ToolCallsEvent,
    ToolDef,
    TurnDoneEvent,
    UsageEvent,
)
from precursor.backend.services.mcp.client import (
    AUTH_PAUSE_TIMEOUT_SECONDS,
    MCPToolAuthRequired,
    MCPToolDef,
    describe_transport_failure,
    get_mcp_client_manager,
    is_transport_failure,
)
from precursor.backend.services.mcp.workspace_links import link_from_result
from precursor.backend.services.meeting_analysis import live_chat_grounding
from precursor.backend.services.message_links import (
    MESSAGE_LINK_INSTRUCTION,
    with_message_reference,
)
from precursor.backend.services.roles import resolve_role_prompt
from precursor.backend.services.suggestions import SUGGESTIONS_INSTRUCTION, split_suggestions
from precursor.backend.services.usage_stats import record_usage
from precursor.backend.services.workspaces import WORKSPACE_READ_TOOLS, WORKSPACE_WRITE_TOOLS

logger = logging.getLogger(__name__)

# Container abstraction: a message belongs to exactly one of a topic or a chat.
ContainerKind = Literal["topic", "chat"]


# -- System context --------------------------------------------------------


async def _plan_delegation_context(
    session: AsyncSession, kind: ContainerKind, container_id: int
) -> list[str]:
    exposed = await resolve_mcp_expose(session)
    if not exposed.get("step_plans"):
        return []
    scope = f"{kind}_id={container_id}"
    parts = [
        "When a follow-up extends or revises an existing deliverable, first find the "
        f"related plan with list_step_plans({scope}) and read it with get_step_plan "
        "(include_artifacts=True). If it is the same work, use amend_step_plan with "
        f"{scope}, the fresh state_token, the user's follow-up, and only the missing "
        "steps; preserve useful completed work and unrelated requirements, include the "
        "user's full follow-up and constraints, and honor any explicit step count. "
        "Consolidation is automatic, so do not add a step just to assemble the final. Supply "
        "complete replacement output instructions only when they need to change; the "
        "follow-up itself already updates the final requirements. Do not introduce "
        "exceptions to retained constraints or additional content the user did not request. "
        "Run the amended plan according to this conversation's autonomy; Plan mode "
        "requires the user to start it. A running plan cannot be amended until its "
        "run has stopped. Do not create a new plan or redo finished steps merely "
        "because the existing plan is complete. If a run pauses, report the actual blocker; "
        "do not claim completion, repeatedly resume a held draft, or produce a substitute "
        "final outside the plan.",
        "When get_step_plan reports filed_in_current_source=true and the final is not stale "
        "or provisional, the complete result is already visible in this conversation. Review "
        "it briefly without repeating each section or posting another copy, unless the user "
        "asks for an additional copy after delivery. Do not claim that a file or external "
        "lookup was or was not made without evidence: an inline result or an incomplete "
        "tool record cannot establish that. A model's score is an estimate, not proof.",
        "Before doing substantial work, choose whether the request needs Step by step. "
        "If the precursor__create_step_plan tool is available and the ask is substantial "
        "and multi-stage, consider delegating it with "
        f"precursor__create_step_plan({scope}, run=True). "
        "This includes several nontrivial, interdependent deliverables that must agree "
        "on shared timings, identifiers, calculations or other constraints, such as a "
        "schedule, risk analysis, instructions and evaluation. Separate drafting and "
        "review can help even when one reply could contain them. Honor an explicit "
        "request not to delegate. "
        "Preserve the complete original request and its constraints "
        "in the delegated ask. Tell the user where to follow the plan and return rather than "
        "waiting for its run, unless the user explicitly asks you to wait; the app handles "
        "review and hand-back according to the conversation's autonomy. An automated run "
        "drafts and checks steps one at a time, not in parallel. This conversation's autonomy "
        "decides who reviews: autonomous, the app files the assessed final and wakes you only "
        "if checks pass; the run can instead stop for approval or review without filing. "
        "Balanced, the user does one final review; manual, the user validates every step. "
        "In the last two you are woken here once they file the result: then carry on by "
        "yourself. In plan mode, nothing runs: the plan is only drafted and the user starts "
        "it. Do not use it for "
        "short or straightforward requests, even if they have several small parts. "
        "If a delegated plan or its run fails to start, say so and retry run_step_plan; "
        "do not silently carry out its steps yourself. A plan may also help when work "
        "needs a skill or a tool-built file and benefits from separate stages. When "
        "delegating such work, put the skill's name in the ask so a step can run it "
        "and file the result back here.",
    ]
    if exposed.get("skills"):
        names = [s.name for s in await skills_service.reconcile_and_list(session) if s.active]
        if names:
            parts.append(
                "Skills a plan's steps can run: " + ", ".join(names[:40]) + ". Name one in "
                "the delegated ask when the job calls for it."
            )
    return parts


async def build_system_context(
    session: AsyncSession, topic: Topic, *, fetch_issue: bool = True
) -> str:
    """Compose system prompt: role persona + long-term memory + topic context + GitHub issue + labels (newest comments first).

    ``fetch_issue=False`` skips the GitHub round trip, for callers that only
    need the prompt's rough size.
    """
    parts: list[str] = [
        "You are Precursor, a focused assistant for the topic below. "
        "Use the linked GitHub issue context (when present) as authoritative; "
        "newer updates and comments outweigh older ones. "
        "When tools are available, prefer fresh tool calls over stale context for "
        "anything that may have changed.",
    ]

    role_prompt = await resolve_role_prompt(session, topic.role)
    if role_prompt:
        parts.append(f"Active assistant role — adopt this persona for every reply:\n{role_prompt}")

    memory_block = await memory_service.build_memory_prompt(session)
    if memory_block:
        parts.append(memory_block)

    parts.append(f"Topic title: {topic.title}")
    if topic.description:
        parts.append(f"Topic description: {topic.description}")
    if topic.seed_content:
        parts.append(side_chats.seed_prompt(topic.seed_content, "topic"))
    parts.extend(await _plan_delegation_context(session, "topic", topic.id))

    repo = await resolve_topic_github_repo(session, topic)
    token = await resolve_github_token(session)
    if fetch_issue and repo and topic.github_issue_number and token:
        try:
            gh = GitHubClient(token=token)
            issue = await gh.get_issue(repo, topic.github_issue_number)
            comments = await gh.list_issue_comments(repo, topic.github_issue_number)
            await gh.aclose()
        except Exception as exc:  # pragma: no cover - network failure is non-fatal
            parts.append(f"(GitHub context unavailable: {exc})")
        else:
            label_names = [
                label["name"] if isinstance(label, dict) else label
                for label in issue.get("labels", [])
            ]
            labels = ", ".join(label_names) or "(none)"
            parts.append(
                f"Linked issue: {repo}#{topic.github_issue_number} — {issue.get('title', '')}"
            )
            parts.append(f"Issue labels: {labels}")
            if issue.get("body"):
                parts.append(f"Issue body:\n{issue['body']}")
            for c in list(reversed(comments))[:10]:
                parts.append(f"Comment by {c['user']} @ {c['updated_at']}:\n{c['body']}")
    artifacts_block = await artifacts_prompt(session, "topic", topic.id)
    if artifacts_block:
        parts.append(artifacts_block)
    parts.append(SUGGESTIONS_INSTRUCTION)
    return "\n\n".join(parts)


async def build_chat_system_context(session: AsyncSession, chat: Chat) -> str:
    """Compose system prompt for a chat: long-term memory + chat title/description.

    Chats are flat, GitHub-free sessions, so this is a lighter version of
    ``build_system_context`` without any issue/label context.
    """
    parts: list[str] = [
        "You are Precursor, a focused assistant. Answer the user's questions "
        "for the chat session below. When tools are available, prefer fresh "
        "tool calls over stale context for anything that may have changed.",
    ]

    role_prompt = await resolve_role_prompt(session, chat.role)
    if role_prompt:
        parts.append(f"Active assistant role — adopt this persona for every reply:\n{role_prompt}")

    memory_block = await memory_service.build_memory_prompt(session)
    if memory_block:
        parts.append(memory_block)

    parts.append(f"Chat title: {chat.title}")
    # In system-prompt mode the description is enforced per user turn (see
    # apply_chat_system_prompt), so we omit the soft context line here to avoid
    # duplicating it. In context mode (default) it rides along as standing
    # discussion-level context.
    if chat.description and not chat.description_as_system_prompt:
        description = await skills_service.expand_references(session, chat.description)
        parts.append(f"Chat description: {description}")

    # A side chat stays grounded in the topic (and reply) it was started from.
    parent_context = await side_chats.build_parent_topic_context(session, chat)
    if parent_context:
        parts.append(parent_context)

    # A step (or consolidation) chat stays grounded in its plan's ask and steps.
    step_context = await step_plans.build_step_context(session, chat)
    if step_context:
        parts.append(step_context)
    elif chat.step_id is None and chat.step_plan_id is None:
        parts.extend(await _plan_delegation_context(session, "chat", chat.id))

    # When this chat is attached to a live meeting session, fold in the current
    # meeting grounding (transcript/insights/notes/…), rebuilt every turn.
    grounding = await live_chat_grounding(session, chat.id)
    if grounding:
        parts.append(grounding)

    artifacts_block = await artifacts_prompt(session, "chat", chat.id)
    if artifacts_block:
        parts.append(artifacts_block)

    parts.append(SUGGESTIONS_INSTRUCTION)
    return "\n\n".join(parts)


def apply_chat_system_prompt(
    chat: Chat,
    history: list[ChatMessage],
    *,
    description: str | None = None,
) -> list[ChatMessage]:
    """Enforce a chat's description as a system instruction on every user turn.

    When ``description_as_system_prompt`` is set and the description is non-empty,
    prepend it to each user message sent to the LLM so the instruction is
    reasserted every turn rather than injected once. Empty description is a
    no-op. Returns a new list; the persisted messages are untouched.

    ``description`` overrides ``chat.description`` — callers pass the
    skill-expanded text (resolved with their DB session) so a description may
    trigger a skill by writing ``/skill-name``.
    """
    raw = chat.description if description is None else description
    description = (raw or "").strip()
    if not (chat.description_as_system_prompt and description):
        return history

    instruction = f"System instruction (must be followed at all times):\n{description}"
    out: list[ChatMessage] = []
    for m in history:
        if m.role == "user":
            prefixed = f"{instruction}\n\n{m.content}" if m.content else instruction
            out.append(ChatMessage(role="user", content=prefixed, image_urls=m.image_urls))
        else:
            out.append(m)
    return out


# -- History hydration -----------------------------------------------------


def hydrate_history(
    rows: list[Message],
    *,
    attachment_max_chars: int = DEFAULT_LLM_MAX_ATTACHMENT_CHARS,
    prompt_override: str | None = None,
    discussion_path: str | None = None,
    legacy_step_prompts: bool = False,
) -> list[ChatMessage]:
    """Turn persisted Messages back into ChatMessages, preserving tool calls.

    Drops orphan assistant-with-tool_calls turns (and any partial tool
    results) whose tool_call ids are not all answered. Anthropic-backed
    models reject such transcripts with a 400; OpenAI tolerates them but
    we want consistent behaviour.

    History starts at the latest compaction marker: the rows before it are
    replaced by its summary (see :func:`compaction_summary_prefix`).

    ``prompt_override`` replaces the content of the last user turn (a skill
    invocation's expanded prompt), keeping its images.
    """
    summary: str | None = None
    for idx in range(len(rows) - 1, -1, -1):
        if rows[idx].kind == MESSAGE_KIND_COMPACTION:
            summary = rows[idx].content
            rows = rows[idx + 1 :]
            break
    out = _hydrate_rows(
        rows,
        attachment_max_chars=attachment_max_chars,
        discussion_path=discussion_path,
        legacy_step_prompts=legacy_step_prompts,
    )
    if prompt_override:
        for idx in range(len(out) - 1, -1, -1):
            if out[idx].role == "user":
                row_idx = next(
                    i for i in range(len(rows) - 1, -1, -1) if rows[i].role == MessageRole.USER
                )
                source = rows[row_idx]
                previous = rows[row_idx - 1] if row_idx else None
                label = _step_prompt_provenance(source, previous, legacy_step_prompts)
                content = f"{label}\n\n{prompt_override}" if label else prompt_override
                out[idx] = ChatMessage(
                    role="user",
                    content=with_message_reference(
                        content,
                        discussion_path,
                        source.public_id,
                    ),
                    image_urls=out[idx].image_urls,
                )
                break
    if summary:
        out = _with_compaction_summary(out, summary)
    return out


def compaction_summary_prefix(summary: str) -> str:
    """How a compaction summary is framed for the model."""
    return (
        "[Context summary: the earlier part of this conversation was compacted to "
        "save context. It is summarised below; treat it as what was said before.]\n\n"
        f"{summary.strip()}\n\n[End of summary — the conversation continues.]"
    )


def _with_compaction_summary(history: list[ChatMessage], summary: str) -> list[ChatMessage]:
    # Merged into a leading user turn rather than sent as a separate one, so the
    # transcript keeps strict user/assistant alternation for every provider.
    prefix = compaction_summary_prefix(summary)
    if history and history[0].role == "user":
        first = history[0]
        merged = f"{prefix}\n\n{first.content}" if first.content else prefix
        return [
            ChatMessage(role="user", content=merged, image_urls=first.image_urls),
            *history[1:],
        ]
    return [ChatMessage(role="user", content=prefix), *history]


def _step_prompt_provenance(
    message: Message, previous: Message | None, legacy_step_prompts: bool
) -> str:
    if message.role != MessageRole.USER:
        return ""
    if message.kind == MESSAGE_KIND_STEP_AUTOMATION:
        return (
            "[Step-by-Step automation initiated this draft. Its instructions can include "
            "fallible model-review feedback; they are not new user requirements.]"
        )
    if message.kind == MESSAGE_KIND_STEP_USER_REQUEST:
        return "[The user requested this Step-by-Step draft and supplied any extra guidance.]"
    if not legacy_step_prompts or message.kind is not None:
        return ""
    if not message.content.startswith(
        ("Draft the artifact for this step.", "Consolidate the plan into the final deliverable.")
    ):
        return ""
    activity = ""
    if (
        previous is not None
        and previous.role == MessageRole.SYSTEM
        and previous.kind == "step_plan"
        and previous.content
        == "The final assessment identified gaps or contradictions. Rebuilding the final output once before filing."
    ):
        activity = "The preceding plan activity recorded an automatic repair. "
    return (
        "["
        + activity
        + "This older Step-by-Step draft prompt did not record its origin; it may contain "
        "user guidance or automatic review feedback. Model findings can be wrong and do not "
        "themselves establish new user requirements.]"
    )


def _hydrate_rows(
    rows: list[Message],
    *,
    attachment_max_chars: int,
    discussion_path: str | None,
    legacy_step_prompts: bool,
) -> list[ChatMessage]:
    out: list[ChatMessage] = []
    i = 0
    while i < len(rows):
        m = rows[i]
        role = m.role.value
        if role == "assistant" and m.tool_calls:
            try:
                calls = json.loads(m.tool_calls)
            except (TypeError, ValueError):
                calls = None
            expected_ids: set[str] = set()
            if isinstance(calls, list):
                for c in calls:
                    cid = c.get("id") if isinstance(c, dict) else None
                    if cid:
                        expected_ids.add(cid)
            # Collect contiguous tool rows that follow.
            tool_msgs: list[ChatMessage] = []
            seen_ids: set[str] = set()
            j = i + 1
            while j < len(rows) and rows[j].role.value == "tool":
                tm = rows[j]
                try:
                    meta = json.loads(tm.tool_calls) if tm.tool_calls else {}
                except (TypeError, ValueError):
                    meta = {}
                tcid = meta.get("tool_call_id") if isinstance(meta, dict) else None
                if tcid:
                    seen_ids.add(tcid)
                tool_msgs.append(
                    ChatMessage(
                        role="tool",
                        content=tm.content,
                        tool_call_id=tcid,
                        name=meta.get("name") if isinstance(meta, dict) else None,
                    )
                )
                j += 1
            if expected_ids and expected_ids.issubset(seen_ids):
                out.append(
                    ChatMessage(
                        role="assistant",
                        content=with_message_reference(m.content, discussion_path, m.public_id)
                        if m.content
                        else "",
                        tool_calls=calls,
                    )
                )
                out.extend(tool_msgs)
            else:
                logger.info(
                    "Dropping orphan tool-call turn (expected %s, got %s)",
                    expected_ids,
                    seen_ids,
                )
            i = j
            continue
        if role == "tool":
            # Orphan tool message with no preceding assistant tool_calls — skip.
            i += 1
            continue
        if role == "system":
            # Persisted SYSTEM rows are UI-only notices (e.g. the "Run now
            # accepted" confirmation). The real system prompt is built fresh
            # each turn, so never feed these to the model.
            i += 1
            continue
        label = _step_prompt_provenance(m, rows[i - 1] if i else None, legacy_step_prompts)
        content = f"{label}\n\n{m.content}" if label else m.content
        image_urls: list[str] = []
        if role == "user" and m.attachments:
            user_attachments = list(m.attachments)
            image_urls = attachments_to_image_urls(
                [att for att in user_attachments if is_image_attachment(att)]
            )
            non_image_context = attachments_to_text_context(
                [att for att in user_attachments if not is_image_attachment(att)],
                max_chars=attachment_max_chars,
            )
            if non_image_context:
                content = f"{content}\n\n{non_image_context}" if content else non_image_context
        out.append(
            ChatMessage(
                role=role,
                content=with_message_reference(content, discussion_path, m.public_id),
                image_urls=image_urls,
            )
        )
        i += 1
    return out


# -- MCP helpers -----------------------------------------------------------


async def load_enabled_mcp_servers(session: AsyncSession) -> list[str]:
    from precursor.backend.models import AppSetting

    row = await session.get(AppSetting, "mcp_enabled")
    if row is None:
        return []
    try:
        data = json.loads(row.value)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    # A disabled or uninstalled plugin's server keeps its toggle, so it comes back
    # as the user left it; until then it isn't registered, and asking for it would
    # fail every turn with "Unknown MCP server".
    manager = get_mcp_client_manager()
    return [name for name, enabled in data.items() if enabled and manager.get(name) is not None]


def mcp_tools_to_provider(tools: list[MCPToolDef]) -> list[ToolDef]:
    return [
        ToolDef(
            name=t.qualified_name,
            description=t.description,
            parameters=t.input_schema,
        )
        for t in tools
    ]


def format_tool_result(payload: Any) -> str:
    """Stringify an MCP tool result for the LLM + UI."""
    # mcp.types.CallToolResult exposes .content as a list of content blocks.
    blocks: list[str] = []
    content = getattr(payload, "content", None)
    if content is None:
        return json.dumps(payload, default=str)
    for block in content:
        text = getattr(block, "text", None)
        if text is not None:
            blocks.append(text)
        else:
            # ``by_alias`` keeps the MCP wire spelling (``mimeType``): MCP 2
            # models dump snake_case field names by default.
            dump = getattr(block, "model_dump", None)
            # Image/audio blocks carry base64 the model can't see as text; keep
            # the block's shape (type, mimeType) but not the payload.
            blocks.append(
                strip_inline_binaries(json.dumps(dump(by_alias=True) if dump else {}, default=str))
            )
    if blocks:
        return "\n\n".join(blocks)
    # Some MCP servers (e.g. the hosted WorkIQ endpoint) return no text content
    # blocks and put the payload in ``structured_content`` instead. Falling back
    # to it keeps reads from looking empty to the model.
    structured = getattr(payload, "structured_content", None)
    if structured is not None:
        return json.dumps(structured, default=str)
    return "(empty result)"


# -- Container persistence helpers -----------------------------------------


def container_message_kwargs(kind: ContainerKind, container_id: int) -> dict[str, int]:
    """Return the FK kwargs to attach a Message to its container."""
    return {"topic_id": container_id} if kind == "topic" else {"chat_id": container_id}


async def publish_container_changed(kind: ContainerKind, container_id: int) -> None:
    if kind == "topic":
        await publish_message_changed(container_id)
    else:
        await publish_message_changed_chat(container_id)


async def _persist_system_message(
    container_id: int, content: str, *, kind: ContainerKind = "topic"
) -> None:
    """Save a system-role message in a fresh session.

    Used for stream errors so they stay in the transcript instead of vanishing
    when the client reloads the persisted history after the stream ends. Uses a
    separate session because the request-scoped one may be closed by the time
    the generator reaches an error.
    """
    async with SessionLocal() as ws:
        ws.add(
            Message(
                role=MessageRole.SYSTEM,
                content=content,
                **container_message_kwargs(kind, container_id),
            )
        )
        await ws.commit()
    await publish_container_changed(kind, container_id)


async def prepare_retry_turn(
    session: AsyncSession,
    *,
    kind: ContainerKind,
    container_id: int,
    message_id: int,
) -> Message:
    """Rewind a container to just after ``message_id`` so that turn can re-run.

    A retry replays an existing user prompt whose turn failed, so nothing new is
    persisted: the original message (and its attachments) is reused and every
    row recorded after it — a partial answer, its tool rows, the `Error: …`
    notice — is deleted. Message ids are monotonic, so "after" is simply a
    greater id.

    Raises ``HTTPException`` when the id doesn't name a user turn of this
    container.
    """
    fk = Message.topic_id if kind == "topic" else Message.chat_id
    result = await session.execute(
        select(Message)
        .where(Message.id == message_id, fk == container_id)
        .options(selectinload(Message.attachments))
    )
    user_msg = result.scalar_one_or_none()
    if user_msg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    if user_msg.role != MessageRole.USER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only a user turn can be retried")

    if kind == "topic":
        await side_chats.detach_messages(
            session, select(Message.id).where(fk == container_id, Message.id > message_id)
        )
    await approval_service.delete_after(session, kind, container_id, message_id)
    await session.execute(delete(Message).where(fk == container_id, Message.id > message_id))
    await session.commit()
    await publish_container_changed(kind, container_id)
    await publish_approval_changed(**container_message_kwargs(kind, container_id))
    return user_msg


# -- The tool loop ---------------------------------------------------------


@dataclass(slots=True)
class AssistantTextDelta:
    """A streamed chunk of assistant text."""

    content: str


@dataclass(slots=True)
class AssistantReasoningDelta:
    """A streamed chunk of the model's thinking for the current round."""

    content: str


@dataclass(slots=True)
class AssistantFinalTurn:
    """The model finished with a plain-text answer (no tool calls)."""

    text: str
    usage: UsageEvent | None
    reasoning: str = ""
    model: str | None = None
    provider: str | None = None


@dataclass(slots=True)
class AssistantToolCallsTurn:
    """The model requested one or more tool calls."""

    text: str
    tool_calls: list[Any]
    openai_tool_calls: list[dict[str, Any]]
    usage: UsageEvent | None
    reasoning: str = ""
    model: str | None = None
    provider: str | None = None


@dataclass(slots=True)
class ToolResultTurn:
    """A single tool call was executed."""

    call: Any
    result_text: str
    is_error: bool
    # ``{"slug", "path"}`` (+ ``written`` for a change) when the tool touched a
    # workspace file, so the UI can
    # offer a link to it without parsing ``result_text`` — which for a read
    # embeds the whole file. None for every other tool.
    link: dict[str, Any] | None = None
    # The approval gate refused the call and the loop answered it instead of parking.
    declined: bool = False


@dataclass(slots=True)
class ToolAuthRequired:
    """A tool the model called needs an interactive sign-in before it can run.

    Yielded at *call* time rather than turn-start time: the catalogue is
    advertised from the stored one even while a credential is stale, so an
    unrelated question is never held hostage by one expired token. The prompt is
    also strictly better than the old blanket one, because it can name the tool
    that actually needs the sign-in.
    """

    server: str
    message: str
    tool: str


@dataclass(slots=True)
class ToolCallOutcome:
    """How one tool call ended, once any sign-in retries are exhausted."""

    result_text: str
    is_error: bool
    # ``{"slug", "path"}`` when the tool touched a workspace file. None otherwise.
    link: dict[str, Any] | None = None


async def call_tool_with_auth_retry(
    *,
    active: Any,
    server: str,
    raw_name: str,
    tool_name: str,
    args: dict[str, Any],
    auth_wait_timeout: float,
) -> AsyncIterator[ToolAuthRequired | ToolCallOutcome]:
    """Run one MCP tool call, pausing for an interactive sign-in if it needs one.

    Yields a :class:`ToolAuthRequired` (at most once) when the call turns out to
    need a sign-in, so each caller can surface it in its own transport, then
    exactly one :class:`ToolCallOutcome`. Shared by the topic/chat tool loop and
    the workspace chat loop so this policy cannot drift between them.

    The pause is bounded by ``auth_wait_timeout`` overall but re-checked every
    ten seconds, retrying the call on each pass. That re-poll matters:
    ``signal_auth_resolved`` is process-global, so a *concurrent* turn's sign-in
    or the keep-alive's silent renewal can fire while we sit between raising and
    registering our waiter, and a single long wait would miss it and park this
    turn for the entire window — the exact stall this whole change exists to
    remove. ``auth_wait_timeout=0`` means "surface the prompt, then give up now",
    which is what an unattended run wants.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, auth_wait_timeout)
    prompted = False
    while True:
        try:
            result = await active.call_tool(server, raw_name, args)
        except MCPToolAuthRequired as exc:
            if not prompted:
                prompted = True
                yield ToolAuthRequired(server=exc.server, message=exc.message, tool=tool_name)
            remaining = deadline - loop.time()
            if remaining > 0:
                # Wake promptly on a sign-in; cap the wait so a signal that fired
                # outside our window is still picked up a few seconds later.
                await active.manager.wait_for_auth(timeout=min(remaining, 10.0))
                continue
            # Never let the model answer as though the tool had run: report the
            # failure so it says what it couldn't reach instead of inventing it.
            yield ToolCallOutcome(
                result_text=(
                    f"Tool '{tool_name}' is unavailable: {exc.server} needs an interactive "
                    f"sign-in ({exc.message}). Sign in from the banner and ask again; do not "
                    "guess the answer."
                ),
                is_error=True,
            )
            return
        except Exception as exc:
            if is_transport_failure(exc):
                # Already retried once on a fresh session inside ``call_tool``,
                # so the endpoint really is down. Say so in terms the model can
                # act on: the bare SDK text ("Session terminated") reads like a
                # local session/auth lapse, which sends it re-planning the call
                # with smaller arguments or telling the user to sign in again —
                # neither of which can help with a remote fault.
                reason = describe_transport_failure(exc)
                logger.warning("MCP call %s(%s) failed: %s", tool_name, args, reason)
                yield ToolCallOutcome(
                    result_text=(
                        f"Tool '{tool_name}' could not reach {server}: {reason}. "
                        "This is a fault on the remote server — not a problem with "
                        "the arguments, and not an expired sign-in. Precursor already "
                        "retried on a fresh session. Do not retry with different "
                        "arguments and do not suggest re-authenticating; tell the user "
                        "the service is unavailable right now, and do not guess the "
                        "answer."
                    ),
                    is_error=True,
                )
                return
            logger.warning("MCP call %s(%s) failed: %s", tool_name, args, exc)
            yield ToolCallOutcome(result_text=f"Tool call failed: {exc}", is_error=True)
            return
        is_error = bool(getattr(result, "is_error", False))
        yield ToolCallOutcome(
            result_text=format_tool_result(result),
            is_error=is_error,
            link=None if is_error else link_from_result(result),
        )
        return


@dataclass(slots=True)
class RoundCapReached:
    """The tool-round budget was exhausted without a final answer."""

    max_tool_rounds: int


@dataclass(slots=True)
class ApprovalsRequested:
    """The round's remaining calls wait on the user, so the turn parks here."""

    items: list[approval_service.PendingCall]


TurnEvent = (
    AssistantTextDelta
    | AssistantReasoningDelta
    | AssistantFinalTurn
    | AssistantToolCallsTurn
    | ToolResultTurn
    | ToolAuthRequired
    | ApprovalsRequested
    | RoundCapReached
)


def _effective_provider(provider: Any) -> str | None:
    return getattr(provider, "effective_provider", None) or getattr(provider, "name", None)


async def execute_tool_call(
    active: Any, call: Any, auth_wait_timeout: float
) -> AsyncIterator[ToolAuthRequired | ToolResultTurn]:
    """Run one model-issued call against ``active``; always ends with its result.

    Shared by the tool loop and the resume of a parked turn, so an approved call
    behaves exactly like one the policy let straight through.
    """
    lookup = active.tool_to_server.get(call.name)
    if lookup is None:
        yield ToolResultTurn(
            call, f"Unknown tool '{call.name}'. No MCP server exposes it.", True, None
        )
        return
    server_name, raw_name = lookup
    try:
        args = json.loads(call.arguments or "{}")
    except json.JSONDecodeError as exc:
        yield ToolResultTurn(call, f"Invalid JSON arguments: {exc}", True, None)
        return
    result_text, is_error, link = "", False, None
    async for step in call_tool_with_auth_retry(
        active=active,
        server=server_name,
        raw_name=raw_name,
        tool_name=call.name,
        args=args,
        auth_wait_timeout=auth_wait_timeout,
    ):
        if isinstance(step, ToolAuthRequired):
            yield step
        else:
            result_text, is_error, link = step.result_text, step.is_error, step.link
    yield ToolResultTurn(call, result_text, is_error, link)


def _ground_plan_calls(
    calls: list[Any],
    tool_to_server: dict[str, tuple[str, str]],
    source: tuple[ContainerKind, int, int] | None,
) -> None:
    """Pin delegation tools to the conversation that is running this turn.

    A model must not create, amend, run or pause plans of another conversation.
    """
    if source is None:
        return
    kind, container_id, message_id = source
    other = "chat_id" if kind == "topic" else "topic_id"
    for call in calls:
        target = tool_to_server.get(call.name)
        if target not in {
            ("precursor", "create_step_plan"),
            ("precursor", "amend_step_plan"),
            ("precursor", "run_step_plan"),
            ("precursor", "pause_step_plan"),
        }:
            continue
        try:
            args = json.loads(call.arguments or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(args, dict):
            continue
        args[f"{kind}_id"] = container_id
        args.pop(other, None)
        if target[1] in ("create_step_plan", "amend_step_plan"):
            args["source_message_id"] = message_id
        call.arguments = json.dumps(args, ensure_ascii=False)


async def run_tool_loop(
    *,
    active: Any,
    provider: Any,
    model: str,
    reasoning_effort: str,
    system_prompt: str,
    history: list[ChatMessage],
    max_tool_rounds: int,
    max_input_tokens: int,
    max_tool_result_tokens: int,
    auth_wait_timeout: float = AUTH_PAUSE_TIMEOUT_SECONDS,
    gate: approval_service.ApprovalGate | None = None,
    extra_tools: Sequence[ToolDef] = (),
    run_extra: Callable[[Any], Awaitable[str]] | None = None,
    extra_permissions: dict[str, MCPToolDef] | None = None,
    decline_note: str | None = None,
    wrap_up: str | None = None,
    source_message: tuple[ContainerKind, int, int] | None = None,
    required_history_message: ChatMessage | None = None,
) -> AsyncIterator[TurnEvent]:
    """Drive the provider + MCP tool loop, yielding semantic turn events.

    Container-agnostic and side-effect-free apart from executing tool calls
    against ``active``: it never persists messages or emits transport events, so
    the SSE and scheduler consumers can apply their own persistence policy while
    sharing this control flow. ``active`` is an acquired MCP session handle
    (exposes ``tools``, ``tool_to_server`` and ``call_tool``).

    ``auth_wait_timeout`` is how long a call may pause for an interactive
    sign-in. Pass ``0`` for unattended runs (the scheduler), where nobody is
    there to complete one: the prompt is still surfaced, but the call fails fast
    with a tool error instead of parking the run for minutes.

    ``gate`` applies an approval policy (chats and topics). A call it holds back,
    or a ``request_approval`` the model raised, gets no tool result: after the
    round's other calls ran, an :class:`ApprovalsRequested` event is yielded and
    the loop ends, leaving the turn parked until the user decides. ``None`` runs
    every call, as workspace chat does.

    The remaining options serve a caller that nobody can answer, such as the step
    assessor: ``decline_note`` answers a held call with "Not run: <why> <note>"
    instead of parking the turn (and ``request_approval`` is not offered);
    ``extra_tools`` are local tools ``run_extra`` executes, shadowing an MCP tool
    of the same name. ``extra_permissions`` gives local tools a read/write identity
    for the gate; gated local tools must use a decline handler rather than park.
    ``wrap_up`` is a last instruction sent with the tools withdrawn on the final
    round, so the model answers instead of hitting the cap.
    ``required_history_message`` makes a bounded caller fail rather than review
    without its input if normal context trimming would discard that message.
    """
    if extra_permissions and gate is not None and decline_note is None:
        raise ValueError("Gated extra tools require a decline handler.")
    tool_to_server = active.tool_to_server  # qualified -> (server, raw_name)
    extra_names = {t.name for t in extra_tools}
    provider_tools = [
        *extra_tools,
        *(t for t in mcp_tools_to_provider(active.tools) if t.name not in extra_names),
    ]
    tool_defs = {t.qualified_name: t for t in active.tools}
    if gate is not None and gate.offers_intent and decline_note is None:
        provider_tools = [*provider_tools, approval_service.REQUEST_APPROVAL_DEF]
        system_prompt = f"{system_prompt}\n\n{approval_service.APPROVAL_PROMPT}"

    system_prompt = f"{system_prompt}\n\n{MESSAGE_LINK_INSTRUCTION}"
    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=system_prompt),
        *history,
    ]

    for _round in range(max_tool_rounds):
        last_round = wrap_up is not None and _round == max_tool_rounds - 1
        if last_round:
            messages.append(ChatMessage(role="user", content=wrap_up or ""))
        text_chunks: list[str] = []
        reasoning_chunks: list[str] = []
        tool_calls: list[Any] = []
        round_usage: UsageEvent | None = None

        trimmed = trim_messages(
            messages,
            max_input_tokens=max_input_tokens,
            per_message_max_tokens=max_tool_result_tokens,
        )
        if required_history_message is not None and not any(
            message is required_history_message for message in trimmed
        ):
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "The assessment input no longer fits the model's context after tool results. "
                "The draft was kept; shorten the input or choose a larger context.",
            )
        async for event in provider.stream_chat_with_tools(
            model=model,
            messages=trimmed,
            tools=[] if last_round else provider_tools,
            reasoning_effort=reasoning_effort,
        ):
            if isinstance(event, TextDeltaEvent):
                text_chunks.append(event.content)
                yield AssistantTextDelta(event.content)
            elif isinstance(event, ReasoningDeltaEvent):
                reasoning_chunks.append(event.content)
                yield AssistantReasoningDelta(event.content)
            elif isinstance(event, ToolCallsEvent):
                tool_calls = event.calls
            elif isinstance(event, UsageEvent):
                round_usage = event
            elif isinstance(event, TurnDoneEvent):
                pass

        assistant_text = "".join(text_chunks)
        # Kept for the transcript only: it never goes back into ``messages``.
        reasoning = "".join(reasoning_chunks).strip()

        if last_round and tool_calls:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "The model requested a tool after tools were withdrawn.",
            )
        if not tool_calls:
            yield AssistantFinalTurn(
                assistant_text,
                round_usage,
                reasoning,
                getattr(provider, "effective_model", None) or model,
                _effective_provider(provider),
            )
            return

        _ground_plan_calls(tool_calls, tool_to_server, source_message)
        openai_tool_calls = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": c.arguments},
            }
            for c in tool_calls
        ]
        yield AssistantToolCallsTurn(
            assistant_text,
            tool_calls,
            openai_tool_calls,
            round_usage,
            reasoning,
            getattr(provider, "effective_model", None) or model,
            _effective_provider(provider),
        )

        messages.append(
            ChatMessage(
                role="assistant",
                content=assistant_text,
                tool_calls=openai_tool_calls,
            )
        )

        parked: list[approval_service.PendingCall] = []
        for call in tool_calls:
            is_extra = run_extra is not None and call.name in extra_names
            extra_def = (extra_permissions or {}).get(call.name) if is_extra else None
            if is_extra:
                server_lookup = (
                    (extra_def.server, extra_def.name) if extra_def is not None else None
                )
            else:
                server_lookup = tool_to_server.get(call.name)
            if (
                gate is not None
                and not is_extra
                and call.name == approval_service.REQUEST_APPROVAL_TOOL
                and decline_note is None
            ):
                if gate.offers_intent:
                    parked.append(approval_service.intent_pending(call.id, call.arguments))
                    continue
            elif gate is not None and server_lookup is not None:
                verdict = gate.check(
                    call.name,
                    server_lookup[0],
                    server_lookup[1],
                    extra_def if is_extra else tool_defs.get(call.name),
                    call.arguments or "{}",
                )
                if verdict.rule_id is not None:
                    await approval_rules_service.touch_rule(verdict.rule_id)
                if verdict.reason is not None and decline_note is not None:
                    declined = f"Not run: {verdict.reason} {decline_note}"
                    yield ToolResultTurn(call, declined, True, None, declined=True)
                    messages.append(
                        ChatMessage(
                            role="tool", content=declined, tool_call_id=call.id, name=call.name
                        )
                    )
                    continue
                if verdict.reason is not None:
                    parked.append(
                        approval_service.PendingCall(
                            call_id=call.id,
                            tool_name=call.name,
                            server=server_lookup[0],
                            raw_tool=server_lookup[1],
                            arguments=call.arguments or "{}",
                            kind=APPROVAL_KIND_TOOL,
                            reason=verdict.reason,
                        )
                    )
                    continue
            if run_extra is not None and is_extra:
                extra_result = await run_extra(call)
                yield ToolResultTurn(call, extra_result, False, None)
                messages.append(
                    ChatMessage(
                        role="tool", content=extra_result, tool_call_id=call.id, name=call.name
                    )
                )
                continue
            outcome: ToolResultTurn | None = None
            async for step in execute_tool_call(active, call, auth_wait_timeout):
                if isinstance(step, ToolAuthRequired):
                    yield step
                else:
                    outcome = step
            assert outcome is not None
            result_text = outcome.result_text
            yield outcome

            messages.append(
                ChatMessage(
                    role="tool",
                    content=result_text,
                    tool_call_id=call.id,
                    name=call.name,
                )
            )

        if parked:
            yield ApprovalsRequested(parked)
            return

    yield RoundCapReached(max_tool_rounds)


# -- Turn persistence ------------------------------------------------------
#
# Shared by the SSE consumer below and the unattended topic turn
# (``services/turn.py``), so a streamed and a scheduled answer are stored the
# same way. Each write uses a fresh session: the request-scoped one may be closed
# by the time a streaming generator gets here.


async def record_round_usage(
    usage: UsageEvent | None,
    *,
    model: str,
    source: str = "chat",
    kind: ContainerKind | None = None,
    container_id: int | None = None,
) -> None:
    """Append one metered model round to the usage ledger (no-op without usage).

    ``kind``/``container_id`` attribute it to a conversation; leave them unset
    for a surface that has none, such as workspace chat.
    """
    if usage is None:
        return
    owner = (
        container_message_kwargs(kind, container_id)
        if kind is not None and container_id is not None
        else {}
    )
    async with SessionLocal() as us:
        await record_usage(
            us,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            source=source,
            model=model,
            **owner,
        )
        await us.commit()


@dataclass(slots=True)
class PersistedAnswer:
    """The stored final answer: its id, clean text and follow-up chips."""

    message_id: int
    public_id: str
    text: str
    suggestions: list[str]


async def persist_final_turn(
    kind: ContainerKind,
    container_id: int,
    ev: AssistantFinalTurn,
    *,
    model: str,
    elapsed_ms: int,
) -> PersistedAnswer:
    """Store the final answer, minus its ``suggest`` block, and meter the round."""
    text, suggestions = split_suggestions(ev.text)
    usage = ev.usage
    async with SessionLocal() as ws:
        assistant = Message(
            role=MessageRole.ASSISTANT,
            content=text,
            reasoning=ev.reasoning or None,
            suggestions=json.dumps(suggestions) if suggestions else None,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            model=model,
            provider=ev.provider,
            elapsed_ms=elapsed_ms,
            **container_message_kwargs(kind, container_id),
        )
        ws.add(assistant)
        await ws.commit()
        await ws.refresh(assistant)
        message_id = assistant.id
        public_id = assistant.public_id
    await publish_container_changed(kind, container_id)
    await record_round_usage(usage, model=model, kind=kind, container_id=container_id)
    return PersistedAnswer(
        message_id=message_id, public_id=public_id, text=text, suggestions=suggestions
    )


async def persist_tool_calls_turn(
    kind: ContainerKind,
    container_id: int,
    ev: AssistantToolCallsTurn,
    *,
    model: str,
) -> tuple[int, str]:
    """Store an assistant-with-tool-calls round; return its database and public ids."""
    usage = ev.usage
    async with SessionLocal() as ws:
        assistant = Message(
            role=MessageRole.ASSISTANT,
            content=ev.text,
            reasoning=ev.reasoning or None,
            tool_calls=json.dumps(ev.openai_tool_calls),
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            **container_message_kwargs(kind, container_id),
        )
        ws.add(assistant)
        await ws.commit()
        await ws.refresh(assistant)
        assistant_id = assistant.id
        public_id = assistant.public_id
    await publish_container_changed(kind, container_id)
    await record_round_usage(usage, model=model, kind=kind, container_id=container_id)
    return assistant_id, public_id


async def persist_tool_result(kind: ContainerKind, container_id: int, ev: ToolResultTurn) -> int:
    """Store one tool result; return its id.

    The workspace ``link`` rides in the row's ``tool_calls`` metadata so the
    transcript's Open chip survives a reload without re-reading the result body.
    """
    call = ev.call
    tool_meta: dict[str, Any] = {
        "tool_call_id": call.id,
        "name": call.name,
        "arguments": call.arguments,
        "is_error": ev.is_error,
    }
    if ev.link:
        tool_meta["link"] = ev.link
    async with SessionLocal() as ws:
        tool_msg = Message(
            role=MessageRole.TOOL,
            content=ev.result_text,
            tool_calls=json.dumps(tool_meta),
            **container_message_kwargs(kind, container_id),
        )
        ws.add(tool_msg)
        await ws.commit()
        await ws.refresh(tool_msg)
        tool_msg_id = tool_msg.id
    await publish_container_changed(kind, container_id)
    # A file the assistant wrote becomes an artifact of this conversation.
    await record_written_file(kind, container_id, ev.link)
    return tool_msg_id


def usage_event(message_id: int, usage: UsageEvent) -> dict[str, str]:
    """The ``usage`` SSE event for a metered round."""
    return {
        "event": "usage",
        "data": json.dumps(
            {
                "message_id": message_id,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }
        ),
    }


# -- SSE consumer ----------------------------------------------------------


async def run_message_stream(
    *,
    kind: ContainerKind,
    container_id: int,
    system_prompt: str,
    history: list[ChatMessage],
    user_echo: dict[str, Any] | None,
    model: str,
    reasoning_effort: str,
    max_tool_rounds: int,
    max_input_tokens: int,
    max_tool_result_tokens: int,
    provider: Any,
    github_token: str,
    enabled_servers: list[str],
    auth_wait_timeout: float = AUTH_PAUSE_TIMEOUT_SECONDS,
    gate: approval_service.ApprovalGate | None = None,
    restrict_precursor: bool = False,
    source_message_id: int | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Container-agnostic SSE generator shared by topic and chat streaming.

    Persists assistant/tool turns against the right container (topic or chat),
    runs the shared MCP tool loop, and yields SSE events. Errors are persisted
    as system messages so they survive a client reload.

    ``auth_wait_timeout`` is how long a tool call may pause for an interactive
    sign-in; it defaults to the interactive window and is threaded through so a
    caller (or a test) can bound it, mirroring the scheduler's run_tool_loop.

    ``user_echo`` is ``None`` when resuming a parked turn: there is no new user
    message to echo. ``gate`` applies the conversation's approval policy.
    """
    manager = get_mcp_client_manager()
    if user_echo is not None and isinstance(user_echo.get("id"), int):
        source_message_id = user_echo["id"]

    if user_echo is not None:
        yield {
            "event": "user_message",
            "data": json.dumps(user_echo),
        }

    # No pre-LLM auth gate: the turn starts now. The catalogue of a server whose
    # credential has lapsed is still advertised (from the stored one), so the
    # model can't quietly answer from memory instead of calling the tool — and a
    # question that never touches that server is no longer held hostage by it.
    # The sign-in is asked for at call time, by the dispatch loop, naming the
    # tool that needs it.
    async with manager.acquired(
        enabled_servers, github_token=github_token, advertise_cached=True
    ) as active:
        if restrict_precursor:
            active.restrict_server_tools("precursor", WORKSPACE_READ_TOOLS | WORKSPACE_WRITE_TOOLS)
        # Warm MCP sessions for the enabled servers (reused across turns). Each
        # contributes tools we aggregate for the LLM; failures are surfaced but
        # don't abort the turn.
        for server_name, err in active.unavailable:
            logger.warning("MCP server %s unavailable: %s", server_name, err)
            if server_name in active.advertised_from_cache:
                # Its tools are on offer and it reconnects on first use, so
                # saying "unavailable" here would be both noisy and wrong.
                continue
            yield {
                "event": "system",
                "data": json.dumps({"message": f"MCP server '{server_name}' unavailable: {err}"}),
            }

        last_assistant_id: int | None = None
        try:
            turn_started = time.monotonic()
            async for ev in run_tool_loop(
                active=active,
                provider=provider,
                model=model,
                reasoning_effort=reasoning_effort,
                system_prompt=system_prompt,
                history=history,
                max_tool_rounds=max_tool_rounds,
                max_input_tokens=max_input_tokens,
                max_tool_result_tokens=max_tool_result_tokens,
                auth_wait_timeout=auth_wait_timeout,
                gate=gate,
                source_message=(kind, container_id, source_message_id)
                if source_message_id is not None
                else None,
            ):
                if isinstance(ev, AssistantTextDelta):
                    yield {
                        "event": "delta",
                        "data": json.dumps({"content": ev.content}),
                    }

                elif isinstance(ev, AssistantReasoningDelta):
                    yield {
                        "event": "reasoning",
                        "data": json.dumps({"content": ev.content}),
                    }

                elif isinstance(ev, AssistantFinalTurn):
                    # Final assistant turn — split off any suggested follow-ups,
                    # persist the clean text, and surface the chips separately.
                    elapsed_ms = int((time.monotonic() - turn_started) * 1000)
                    actual_model = ev.model or model
                    answer = await persist_final_turn(
                        kind, container_id, ev, model=actual_model, elapsed_ms=elapsed_ms
                    )
                    if kind == "chat":
                        # Imported here: step_plan_llm runs turns through this module.
                        from precursor.backend.services.step_plan_llm import apply_chat_reply

                        await apply_chat_reply(container_id, answer.text)
                    if ev.usage is not None:
                        yield usage_event(answer.message_id, ev.usage)
                    yield {
                        "event": "done",
                        "data": json.dumps(
                            {
                                "id": answer.message_id,
                                "public_id": answer.public_id,
                                "content": answer.text,
                                "model": actual_model,
                                "provider": ev.provider,
                                "elapsed_ms": elapsed_ms,
                            }
                        ),
                    }
                    if answer.suggestions:
                        yield {
                            "event": "suggestions",
                            "data": json.dumps(
                                {"message_id": answer.message_id, "items": answer.suggestions}
                            ),
                        }
                    return

                elif isinstance(ev, AssistantToolCallsTurn):
                    assistant_id, public_id = await persist_tool_calls_turn(
                        kind, container_id, ev, model=ev.model or model
                    )
                    last_assistant_id = assistant_id
                    if ev.usage is not None:
                        yield usage_event(assistant_id, ev.usage)
                    yield {
                        "event": "tool_calls",
                        "data": json.dumps(
                            {
                                "assistant_id": assistant_id,
                                "public_id": public_id,
                                "calls": [
                                    {
                                        "id": c.id,
                                        "name": c.name,
                                        "arguments": c.arguments,
                                    }
                                    for c in ev.tool_calls
                                ],
                            }
                        ),
                    }

                elif isinstance(ev, ToolResultTurn):
                    tool_msg_id = await persist_tool_result(kind, container_id, ev)
                    yield {
                        "event": "tool_result",
                        "data": json.dumps(
                            {
                                "message_id": tool_msg_id,
                                "tool_call_id": ev.call.id,
                                "name": ev.call.name,
                                "arguments": ev.call.arguments,
                                "content": ev.result_text,
                                "is_error": ev.is_error,
                                "link": ev.link,
                            }
                        ),
                    }

                elif isinstance(ev, ToolAuthRequired):
                    # The model reached for a tool whose credential has lapsed.
                    # This is the moment the sign-in is genuinely required, so
                    # prompt now — naming the tool — rather than at turn start.
                    yield {
                        "event": "mcp_auth_required",
                        "data": json.dumps(
                            {
                                "server": ev.server,
                                "message": ev.message,
                                "tool": ev.tool,
                            }
                        ),
                    }

                elif isinstance(ev, ApprovalsRequested):
                    async with SessionLocal() as ws:
                        rows = await approval_service.record_pending(
                            ws,
                            kind,
                            container_id,
                            last_assistant_id,
                            ev.items,
                            source_message_id=source_message_id,
                        )
                        payload = [approval_service.serialize(r) for r in rows]
                    await publish_approval_changed(**container_message_kwargs(kind, container_id))
                    yield {
                        "event": "approval_required",
                        "data": json.dumps({"approvals": payload}),
                    }
                    return

                elif isinstance(ev, RoundCapReached):
                    # Hit the iteration cap.
                    cap_msg = f"Stopped after {ev.max_tool_rounds} tool rounds."
                    await _persist_system_message(container_id, f"Error: {cap_msg}", kind=kind)
                    yield {
                        "event": "error",
                        "data": json.dumps({"message": cap_msg}),
                    }
        except LLMError as exc:
            # Provider rejected the request for a reason worth showing the
            # user (too many tools, bad credentials, …) — surface it cleanly
            # without a crash-style traceback.
            logger.warning("Chat stream rejected by provider: %s", exc)
            await _persist_system_message(container_id, f"Error: {exc}", kind=kind)
            yield {"event": "error", "data": json.dumps({"message": str(exc)})}
        except Exception as exc:
            logger.exception("Chat stream failed")
            await _persist_system_message(container_id, f"Error: {exc}", kind=kind)
            yield {"event": "error", "data": json.dumps({"message": str(exc)})}


def lifecycle_stream(
    kind: ContainerKind,
    container_id: int,
    inner: AsyncIterator[dict[str, str]],
) -> AsyncIterator[dict[str, str]]:
    """Wrap an event stream with stream.started / stream.ended publishing."""

    async def gen() -> AsyncIterator[dict[str, str]]:
        if kind == "topic":
            await publish_stream_started(container_id)
        else:
            await publish_stream_started_chat(container_id)
        try:
            async for evt in inner:
                yield evt
        finally:
            if kind == "topic":
                await publish_stream_ended(container_id)
            else:
                await publish_stream_ended_chat(container_id)

    return gen()
