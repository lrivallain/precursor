"""Workspace chat — the ephemeral authoring assistant bound to the active file.

Unlike topics and chats nothing is persisted: the client keeps the history and
sends it with every turn. The model still runs through the shared
:func:`~precursor.backend.services.turn_engine.run_tool_loop`, so the tool loop,
the call-time sign-in pause and context trimming are the ones every other
surface uses, and each metered round lands in the usage ledger. This module only
builds the prompt and maps the loop's events onto the SSE stream
``WorkspaceChat.tsx`` consumes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import Workspace
from precursor.backend.schemas.workspace import WorkspaceChatRequest
from precursor.backend.services import workspace_fs as fs
from precursor.backend.services.conversation_turn import TurnSettings
from precursor.backend.services.llm.base import ChatMessage
from precursor.backend.services.mcp.client import (
    AUTH_PAUSE_TIMEOUT_SECONDS,
    get_mcp_client_manager,
)
from precursor.backend.services.roles import resolve_role_prompt
from precursor.backend.services.suggestions import SUGGESTIONS_INSTRUCTION, split_suggestions
from precursor.backend.services.turn_engine import (
    AssistantFinalTurn,
    AssistantTextDelta,
    AssistantToolCallsTurn,
    RoundCapReached,
    ToolAuthRequired,
    ToolResultTurn,
    record_round_usage,
    run_tool_loop,
)

logger = logging.getLogger(__name__)

# Workspace rounds have no conversation to attribute them to, so the ledger
# tells them apart by source instead.
USAGE_SOURCE = "workspace"

_SYSTEM_PROMPT = (
    "You are a writing assistant helping the user author and improve Markdown "
    "knowledge-base content. Be concise and practical. When proposing changes "
    "to a file, return Markdown the user can paste directly. Do not invent "
    "facts; ask for clarification when the source material is insufficient."
)


def _workspace_tool_context(ws: Workspace, path: str | None) -> str:
    """Tell the model which workspace it's operating on for file tools.

    The workspace-fs MCP tools take a ``workspace_id``; surfacing it (and the
    active file) means the model doesn't have to call ``list_workspaces`` first.
    """
    lines = [
        "\n\nWorkspace tools (if enabled) operate on this workspace:",
        f"- workspace_id: {ws.id}",
        f"- slug: {ws.slug}",
        f"- name: {ws.name}",
    ]
    if path:
        lines.append(f"- the user is currently viewing the file: {path}")
    lines.append(
        "When using workspace filesystem tools, pass this workspace_id and use "
        "paths relative to the workspace root."
    )
    return "\n".join(lines)


async def build_workspace_system_prompt(
    session: AsyncSession, ws: Workspace, root: Path, path: str | None
) -> str:
    """Compose the system prompt: persona, the active file, tool context, role."""
    file_context = ""
    if path:
        try:
            content = fs.read_text(root, path)
        except (
            fs.UnsafePathError,
            FileNotFoundError,
            IsADirectoryError,
            UnicodeDecodeError,
        ):
            pass
        else:
            file_context = (
                f"\n\nThe user is currently editing `{path}`. "
                f"Its current content is:\n\n```markdown\n{content}\n```"
            )

    system_prompt = _SYSTEM_PROMPT + file_context + _workspace_tool_context(ws, path)
    role_prompt = await resolve_role_prompt(session, ws.role_id)
    if role_prompt:
        system_prompt += (
            f"\n\nActive assistant role — adopt this persona for every reply:\n{role_prompt}"
        )
    return f"{system_prompt}\n\n{SUGGESTIONS_INSTRUCTION}"


def workspace_history(payload: WorkspaceChatRequest) -> list[ChatMessage]:
    """The client-kept history plus this turn, as the model should see it.

    Skills: the UI shows the literal ``content``, but the model receives the
    expanded ``prompt_override`` for this turn only.
    """
    history = [ChatMessage(role=turn.role, content=turn.content) for turn in payload.history]
    history.append(ChatMessage(role="user", content=payload.prompt_override or payload.content))
    return history


def _event(name: str, data: object) -> dict[str, str]:
    return {"event": name, "data": json.dumps(data)}


async def run_workspace_stream(
    *,
    system_prompt: str,
    history: list[ChatMessage],
    settings: TurnSettings,
    auth_wait_timeout: float = AUTH_PAUSE_TIMEOUT_SECONDS,
) -> AsyncIterator[dict[str, str]]:
    """Run one workspace chat turn, yielding the workspace SSE events.

    Events carry no message ids — nothing is stored — but otherwise match the
    topic/chat stream, ``link`` on ``tool_result`` included.
    """
    manager = get_mcp_client_manager()
    # No pre-LLM auth gate: the turn starts now. A server whose credential has
    # lapsed still contributes its stored catalogue, so the model can't quietly
    # answer from memory instead of calling the tool, and a question that never
    # touches that server isn't held hostage by it. The sign-in is requested at
    # call time, naming the tool that needs it.
    async with manager.acquired(
        settings.enabled_servers, github_token=settings.github_token, advertise_cached=True
    ) as active:
        for server_name, err in active.unavailable:
            logger.warning("Workspace chat: MCP server %s unavailable: %s", server_name, err)
            if server_name in active.advertised_from_cache:
                continue
            yield _event("system", {"message": f"MCP server '{server_name}' unavailable: {err}"})

        try:
            async for ev in run_tool_loop(
                active=active,
                provider=settings.provider,
                model=settings.model,
                reasoning_effort=settings.reasoning_effort,
                system_prompt=system_prompt,
                history=history,
                max_tool_rounds=settings.max_tool_rounds,
                max_input_tokens=settings.max_input_tokens,
                max_tool_result_tokens=settings.max_tool_result_tokens,
                auth_wait_timeout=auth_wait_timeout,
            ):
                if isinstance(ev, AssistantTextDelta):
                    yield _event("delta", {"content": ev.content})

                elif isinstance(ev, AssistantToolCallsTurn):
                    await record_round_usage(ev.usage, model=settings.model, source=USAGE_SOURCE)
                    yield _event(
                        "tool_calls",
                        {
                            "calls": [
                                {"id": c.id, "name": c.name, "arguments": c.arguments}
                                for c in ev.tool_calls
                            ]
                        },
                    )

                elif isinstance(ev, ToolResultTurn):
                    yield _event(
                        "tool_result",
                        {
                            "tool_call_id": ev.call.id,
                            "name": ev.call.name,
                            "arguments": ev.call.arguments,
                            "content": ev.result_text,
                            "is_error": ev.is_error,
                            "link": ev.link,
                        },
                    )

                elif isinstance(ev, ToolAuthRequired):
                    yield _event(
                        "mcp_auth_required",
                        {"server": ev.server, "message": ev.message, "tool": ev.tool},
                    )

                elif isinstance(ev, AssistantFinalTurn):
                    await record_round_usage(ev.usage, model=settings.model, source=USAGE_SOURCE)
                    clean, suggestions = split_suggestions(ev.text)
                    yield _event("done", {"content": clean})
                    if suggestions:
                        yield _event("suggestions", {"items": suggestions})
                    return

                elif isinstance(ev, RoundCapReached):
                    yield _event(
                        "error", {"message": f"Stopped after {ev.max_tool_rounds} tool rounds."}
                    )
        except Exception as exc:
            logger.exception("Workspace chat failed")
            yield _event("error", {"message": str(exc)})
