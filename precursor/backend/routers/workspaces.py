"""Workspace endpoints — Git-backed Markdown authoring with AI help.

Workspaces are working copies of GitHub repositories. The browser/editor
operate on files relative to the workspace's (optional) subdir; git sync
operates on the repository root. Chat is ephemeral assist over the active
file's content.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from precursor.backend.config import get_settings
from precursor.backend.db import get_session
from precursor.backend.models import Workspace
from precursor.backend.schemas import (
    CommitRequest,
    FileContent,
    FileCreate,
    FileDiff,
    FileNode,
    FileRename,
    FileWrite,
    FolderCreate,
    GitActionResult,
    GitStatus,
    LocalPath,
    WorkspaceCreate,
    WorkspaceRead,
    WorkspaceUpdate,
)
from precursor.backend.schemas.workspace import WorkspaceChatRequest
from precursor.backend.services import workspace_fs as fs
from precursor.backend.services import workspace_git as git
from precursor.backend.services.app_settings import (
    resolve_llm_model,
    resolve_llm_reasoning_effort,
    resolve_max_tool_rounds,
)
from precursor.backend.services.context_budget import trim_messages
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.llm import get_llm_provider
from precursor.backend.services.llm.base import (
    ChatMessage,
    TextDeltaEvent,
    ToolCallsEvent,
    TurnDoneEvent,
    UsageEvent,
)
from precursor.backend.services.mcp.client import (
    AUTH_PAUSE_TIMEOUT_SECONDS,
    get_mcp_client_manager,
)
from precursor.backend.services.roles import resolve_role_prompt
from precursor.backend.services.slugs import slugify
from precursor.backend.services.suggestions import (
    SUGGESTIONS_INSTRUCTION,
    split_suggestions,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


def workspace_root(ws: Workspace) -> Path:
    """Repository working-copy root on disk."""
    return Path(get_settings().workspaces_dir) / ws.slug


def browse_root(ws: Workspace) -> Path:
    """File-browser root (repo root, or the configured subdir within it)."""
    root = workspace_root(ws)
    if ws.subdir:
        return root / ws.subdir.strip("/")
    return root


async def get_workspace_by_slug(slug: str, session: AsyncSession) -> Workspace | None:
    return (
        await session.execute(select(Workspace).where(Workspace.slug == slug))
    ).scalar_one_or_none()


async def _get_workspace(workspace_id: int, session: AsyncSession) -> Workspace:
    ws = await session.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    return ws


async def _get_git_workspace(workspace_id: int, session: AsyncSession) -> Workspace:
    """Fetch a workspace and reject git operations on local (non-git) ones."""
    ws = await _get_workspace(workspace_id, session)
    if ws.kind != "git":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This is a local workspace — git operations are not available.",
        )
    return ws


async def _allocate_slug(session: AsyncSession, base: str) -> str:
    base = base or "workspace"
    candidate = base
    n = 2
    while True:
        existing = (
            await session.execute(select(Workspace.id).where(Workspace.slug == candidate))
        ).first()
        if existing is None:
            return candidate
        candidate = f"{base}-{n}"
        n += 1


# --------------------------------------------------------------------------
# Workspace CRUD
# --------------------------------------------------------------------------


@router.get("", response_model=list[WorkspaceRead])
async def list_workspaces(
    session: AsyncSession = Depends(get_session),
) -> list[Workspace]:
    result = await session.execute(select(Workspace).order_by(Workspace.created_at.desc()))
    return list(result.scalars().all())


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    session: AsyncSession = Depends(get_session),
) -> Workspace:
    is_local = payload.kind == "local"
    if not is_local and not (payload.repo_url or "").strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A repository URL is required for a git workspace.",
        )
    if not is_local and not git.git_available():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "git is not installed on the server — install it to use git workspaces.",
        )

    slug = slugify(payload.slug or payload.name)
    slug = await _allocate_slug(session, slug)

    ws = Workspace(
        name=payload.name,
        slug=slug,
        kind="local" if is_local else "git",
        repo_url=None if is_local else (payload.repo_url or "").strip(),
        branch=payload.branch.strip() or "main",
        subdir=(payload.subdir or "").strip() or None,
    )
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    dest = workspace_root(ws)
    if is_local:
        # A local workspace is just an empty folder we own under workspaces_dir.
        dest.mkdir(parents=True, exist_ok=True)
    else:
        # Clone inline. On failure, keep the row so the user can retry via re-clone.
        token = await resolve_github_token(session)
        try:
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            await git.clone(ws.repo_url or "", dest, ws.branch, token)
        except git.GitError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    ws.cloned_at = datetime.now(UTC)
    ws.last_synced_at = ws.cloned_at
    await session.commit()
    await session.refresh(ws)
    return ws


@router.patch("/{workspace_id}", response_model=WorkspaceRead)
async def update_workspace(
    workspace_id: int,
    payload: WorkspaceUpdate,
    session: AsyncSession = Depends(get_session),
) -> Workspace:
    """Update mutable workspace fields (currently the assigned Assistant Role)."""
    ws = await _get_workspace(workspace_id, session)
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(ws, key, value)
    await session.commit()
    await session.refresh(ws)
    return ws


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(workspace_id: int, session: AsyncSession = Depends(get_session)) -> None:
    ws = await _get_workspace(workspace_id, session)
    dest = workspace_root(ws)
    await session.delete(ws)
    await session.commit()
    # Remove the working copy from disk after the row is gone.
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)


@router.get("/{workspace_id}/local-path", response_model=LocalPath)
async def workspace_local_path(
    workspace_id: int, session: AsyncSession = Depends(get_session)
) -> LocalPath:
    """Absolute path of the workspace's working copy, for use in a terminal/editor."""
    ws = await _get_workspace(workspace_id, session)
    return LocalPath(path=str(workspace_root(ws).resolve()))


# --------------------------------------------------------------------------
# File browser / editor
# --------------------------------------------------------------------------


@router.get("/{workspace_id}/files", response_model=list[FileNode])
async def list_files(
    workspace_id: int, session: AsyncSession = Depends(get_session)
) -> list[FileNode]:
    ws = await _get_workspace(workspace_id, session)
    return fs.list_tree(browse_root(ws))


@router.get("/{workspace_id}/file", response_model=FileContent)
async def read_file(
    workspace_id: int,
    path: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> FileContent:
    ws = await _get_workspace(workspace_id, session)
    try:
        content = fs.read_text(browse_root(ws), path)
    except fs.UnsafePathError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found") from exc
    except UnicodeDecodeError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Not a text file") from exc
    return FileContent(path=path, content=content)


@router.put("/{workspace_id}/file", response_model=FileContent)
async def write_file(
    workspace_id: int,
    payload: FileWrite,
    path: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> FileContent:
    ws = await _get_workspace(workspace_id, session)
    try:
        fs.write_text(browse_root(ws), path, payload.content)
    except fs.UnsafePathError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return FileContent(path=path, content=payload.content)


@router.post(
    "/{workspace_id}/file",
    response_model=FileContent,
    status_code=status.HTTP_201_CREATED,
)
async def create_file_endpoint(
    workspace_id: int,
    payload: FileCreate,
    session: AsyncSession = Depends(get_session),
) -> FileContent:
    ws = await _get_workspace(workspace_id, session)
    try:
        fs.create_file(browse_root(ws), payload.path, payload.content)
    except fs.UnsafePathError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "File already exists") from exc
    return FileContent(path=payload.path, content=payload.content)


@router.post(
    "/{workspace_id}/folder",
    response_model=FileNode,
    status_code=status.HTTP_201_CREATED,
)
async def create_folder_endpoint(
    workspace_id: int,
    payload: FolderCreate,
    session: AsyncSession = Depends(get_session),
) -> FileNode:
    ws = await _get_workspace(workspace_id, session)
    path = payload.path.strip().strip("/")
    try:
        fs.create_dir(browse_root(ws), path)
    except fs.UnsafePathError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Folder already exists") from exc
    return FileNode(path=path, name=path.rsplit("/", 1)[-1], type="dir")


@router.delete("/{workspace_id}/file", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file_endpoint(
    workspace_id: int,
    path: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> None:
    ws = await _get_workspace(workspace_id, session)
    try:
        fs.delete_file(browse_root(ws), path)
    except fs.UnsafePathError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found") from exc


@router.post("/{workspace_id}/rename", response_model=FileNode)
async def rename_endpoint(
    workspace_id: int,
    payload: FileRename,
    session: AsyncSession = Depends(get_session),
) -> FileNode:
    ws = await _get_workspace(workspace_id, session)
    src = payload.path.strip().strip("/")
    dst = payload.new_path.strip().strip("/")
    root = browse_root(ws)
    try:
        fs.rename(root, src, dst)
        node_type = "dir" if fs.safe_join(root, dst).is_dir() else "file"
    except fs.UnsafePathError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found") from exc
    except FileExistsError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A file or folder already exists at the destination"
        ) from exc
    return FileNode(path=dst, name=dst.rsplit("/", 1)[-1], type=node_type)


# --------------------------------------------------------------------------
# Git sync
# --------------------------------------------------------------------------


@router.get("/{workspace_id}/git/status", response_model=GitStatus)
async def git_status(workspace_id: int, session: AsyncSession = Depends(get_session)) -> GitStatus:
    ws = await _get_git_workspace(workspace_id, session)
    try:
        return await git.status(workspace_root(ws))
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/{workspace_id}/git/pull", response_model=GitActionResult)
async def git_pull(
    workspace_id: int, session: AsyncSession = Depends(get_session)
) -> GitActionResult:
    ws = await _get_git_workspace(workspace_id, session)
    token = await resolve_github_token(session)
    root = workspace_root(ws)
    try:
        ok, detail = await git.pull(root, ws.branch, token)
        st = await git.status(root)
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if ok:
        ws.last_synced_at = datetime.now(UTC)
        await session.commit()
    return GitActionResult(
        ok=ok,
        detail=detail,
        needs_manual_merge=not ok,
        local_path=str(root),
        status=st,
    )


@router.post("/{workspace_id}/git/commit-push", response_model=GitActionResult)
async def git_commit_push(
    workspace_id: int,
    payload: CommitRequest,
    session: AsyncSession = Depends(get_session),
) -> GitActionResult:
    ws = await _get_git_workspace(workspace_id, session)
    token = await resolve_github_token(session)
    root = workspace_root(ws)
    try:
        if payload.paths is not None:
            committed, commit_detail = await git.commit_paths(root, payload.message, payload.paths)
        else:
            committed, commit_detail = await git.commit_all(root, payload.message)
        if not committed:
            st = await git.status(root)
            return GitActionResult(ok=False, detail=commit_detail, local_path=str(root), status=st)
        pushed, push_detail = await git.push(root, ws.branch, token)
        st = await git.status(root)
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if pushed:
        ws.last_synced_at = datetime.now(UTC)
        await session.commit()
    return GitActionResult(
        ok=pushed,
        detail=push_detail if pushed else f"Committed locally but push failed: {push_detail}",
        needs_manual_merge=not pushed,
        local_path=str(root),
        status=st,
    )


@router.post("/{workspace_id}/git/discard", response_model=GitStatus)
async def git_discard(
    workspace_id: int,
    path: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> GitStatus:
    ws = await _get_git_workspace(workspace_id, session)
    root = workspace_root(ws)
    try:
        await git.discard(root, path)
        return await git.status(root)
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/{workspace_id}/git/diff", response_model=FileDiff)
async def git_diff(
    workspace_id: int,
    path: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> FileDiff:
    ws = await _get_git_workspace(workspace_id, session)
    root = workspace_root(ws)
    try:
        diff, binary = await git.diff_file(root, path)
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return FileDiff(path=path, diff=diff, binary=binary)


# --------------------------------------------------------------------------
# Chat — ephemeral authoring assistant bound to the active file
# --------------------------------------------------------------------------

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


@router.post("/{workspace_id}/chat/stream")
async def chat_stream(
    workspace_id: int,
    payload: WorkspaceChatRequest,
    session: AsyncSession = Depends(get_session),
) -> EventSourceResponse:
    # Reuse the proven tool-loop helpers from the main chat router. Imported
    # lazily to keep the module import graph flat (chat.py imports nothing here).
    from precursor.backend.routers.chat import (
        _format_tool_result,
        _load_enabled_mcp_servers,
        _mcp_tools_to_provider,
    )

    ws = await _get_workspace(workspace_id, session)

    file_context = ""
    if payload.path:
        try:
            content = fs.read_text(browse_root(ws), payload.path)
            file_context = (
                f"\n\nThe user is currently editing `{payload.path}`. "
                f"Its current content is:\n\n```markdown\n{content}\n```"
            )
        except (
            fs.UnsafePathError,
            FileNotFoundError,
            IsADirectoryError,
            UnicodeDecodeError,
        ):
            file_context = ""

    settings = get_settings()
    model = payload.model or await resolve_llm_model(session)
    reasoning_effort = await resolve_llm_reasoning_effort(session)
    max_tool_rounds = await resolve_max_tool_rounds(session)
    enabled_servers = await _load_enabled_mcp_servers(session)
    provider = await get_llm_provider(session)
    github_token = await resolve_github_token(session)
    manager = get_mcp_client_manager()

    system_prompt = _SYSTEM_PROMPT + file_context + _workspace_tool_context(ws, payload.path)
    role_prompt = await resolve_role_prompt(session, ws.role_id)
    if role_prompt:
        system_prompt += (
            f"\n\nActive assistant role — adopt this persona for every reply:\n{role_prompt}"
        )
    system_prompt += f"\n\n{SUGGESTIONS_INSTRUCTION}"

    base_messages: list[ChatMessage] = [
        ChatMessage(role="system", content=system_prompt),
    ]
    for turn in payload.history:
        base_messages.append(ChatMessage(role=turn.role, content=turn.content))
    # Skills: the UI shows the literal `content`, but the model receives the
    # expanded `prompt_override` for this turn only.
    base_messages.append(
        ChatMessage(role="user", content=payload.prompt_override or payload.content)
    )

    async def event_stream() -> AsyncIterator[dict[str, str]]:
        # Pause-and-resume gate: if an enabled server needs an interactive
        # sign-in, hold the turn and surface the auth prompt instead of running
        # the LLM with its tools missing (which yields confident, hallucinated
        # answers). Resume once the user signs in. Mirrors the topic/chat flow.
        if enabled_servers:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + AUTH_PAUSE_TIMEOUT_SECONDS
            announced: set[str] = set()
            while True:
                async with manager.acquired(enabled_servers, github_token=github_token) as probe:
                    blocked = manager.auth_blocked_servers([n for n, _ in probe.unavailable])
                if not blocked:
                    break
                for name in blocked:
                    if name in announced:
                        continue
                    announced.add(name)
                    entry = manager.get(name)
                    yield {
                        "event": "mcp_auth_required",
                        "data": json.dumps(
                            {
                                "server": name,
                                "message": (entry.error if entry else None) or "Sign-in required.",
                            }
                        ),
                    }
                remaining = deadline - loop.time()
                if remaining <= 0:
                    yield {
                        "event": "system",
                        "data": json.dumps(
                            {
                                "message": (
                                    "Sign-in wasn't completed in time, so I stopped instead of "
                                    "answering without "
                                    f"{', '.join(sorted(announced))}. Send your message again "
                                    "after signing in."
                                )
                            }
                        ),
                    }
                    return
                await manager.wait_for_auth(timeout=min(remaining, 10.0))

        async with manager.acquired(enabled_servers, github_token=github_token) as active:
            tool_to_server = active.tool_to_server
            for server_name, err in active.unavailable:
                logger.warning(
                    "Workspace chat: MCP server %s unavailable: %s",
                    server_name,
                    err,
                )
                yield {
                    "event": "system",
                    "data": json.dumps(
                        {"message": f"MCP server '{server_name}' unavailable: {err}"}
                    ),
                }

            provider_tools = _mcp_tools_to_provider(active.tools)
            messages = list(base_messages)

            # No tools enabled → simple text stream (matches the old behaviour).
            if not provider_tools:
                try:
                    text_chunks: list[str] = []
                    async for delta in provider.stream_chat(
                        model=model, messages=messages, reasoning_effort=reasoning_effort
                    ):
                        text_chunks.append(delta)
                        yield {
                            "event": "delta",
                            "data": json.dumps({"content": delta}),
                        }
                    clean, suggestions = split_suggestions("".join(text_chunks))
                    yield {"event": "done", "data": json.dumps({"content": clean})}
                    if suggestions:
                        yield {
                            "event": "suggestions",
                            "data": json.dumps({"items": suggestions}),
                        }
                except Exception as exc:
                    logger.exception("Workspace chat failed")
                    yield {"event": "error", "data": json.dumps({"message": str(exc)})}
                return

            try:
                for _round in range(max_tool_rounds):
                    text_chunks = []
                    tool_calls: list[Any] = []

                    async for event in provider.stream_chat_with_tools(
                        model=model,
                        messages=trim_messages(
                            messages,
                            max_input_tokens=settings.llm_max_input_tokens,
                            per_message_max_tokens=settings.llm_max_tool_result_tokens,
                        ),
                        tools=provider_tools,
                        reasoning_effort=reasoning_effort,
                    ):
                        if isinstance(event, TextDeltaEvent):
                            text_chunks.append(event.content)
                            yield {
                                "event": "delta",
                                "data": json.dumps({"content": event.content}),
                            }
                        elif isinstance(event, ToolCallsEvent):
                            tool_calls = event.calls
                        elif isinstance(event, UsageEvent | TurnDoneEvent):
                            pass

                    assistant_text = "".join(text_chunks)

                    if not tool_calls:
                        clean, suggestions = split_suggestions(assistant_text)
                        yield {
                            "event": "done",
                            "data": json.dumps({"content": clean}),
                        }
                        if suggestions:
                            yield {
                                "event": "suggestions",
                                "data": json.dumps({"items": suggestions}),
                            }
                        return

                    openai_tool_calls = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": c.arguments},
                        }
                        for c in tool_calls
                    ]
                    yield {
                        "event": "tool_calls",
                        "data": json.dumps(
                            {
                                "calls": [
                                    {
                                        "id": c.id,
                                        "name": c.name,
                                        "arguments": c.arguments,
                                    }
                                    for c in tool_calls
                                ],
                            }
                        ),
                    }
                    messages.append(
                        ChatMessage(
                            role="assistant",
                            content=assistant_text,
                            tool_calls=openai_tool_calls,
                        )
                    )

                    for call in tool_calls:
                        server_lookup = tool_to_server.get(call.name)
                        is_error = False
                        if server_lookup is None:
                            result_text = f"Unknown tool '{call.name}'. No MCP server exposes it."
                            is_error = True
                        else:
                            server_name, raw_name = server_lookup
                            try:
                                args = json.loads(call.arguments or "{}")
                            except json.JSONDecodeError as exc:
                                args = None
                                result_text = f"Invalid JSON arguments: {exc}"
                                is_error = True
                            if args is not None:
                                try:
                                    result = await active.call_tool(server_name, raw_name, args)
                                    result_text = _format_tool_result(result)
                                    is_error = bool(getattr(result, "isError", False))
                                except Exception as exc:
                                    logger.warning(
                                        "Workspace chat: MCP call %s failed: %s",
                                        call.name,
                                        exc,
                                    )
                                    result_text = f"Tool call failed: {exc}"
                                    is_error = True

                        yield {
                            "event": "tool_result",
                            "data": json.dumps(
                                {
                                    "tool_call_id": call.id,
                                    "name": call.name,
                                    "arguments": call.arguments,
                                    "content": result_text,
                                    "is_error": is_error,
                                }
                            ),
                        }
                        messages.append(
                            ChatMessage(
                                role="tool",
                                content=result_text,
                                tool_call_id=call.id,
                                name=call.name,
                            )
                        )

                yield {
                    "event": "error",
                    "data": json.dumps(
                        {"message": f"Stopped after {max_tool_rounds} tool rounds."}
                    ),
                }
            except Exception as exc:
                logger.exception("Workspace chat failed")
                yield {"event": "error", "data": json.dumps({"message": str(exc)})}

    return EventSourceResponse(event_stream())
