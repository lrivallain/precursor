"""Built-in MCP server: Precursor's own capabilities (outbound).

Runs as a stdio subprocess (like ``fetch_server``).
This is how Precursor exposes *itself* over MCP: the same in-tree entrypoint
serves the app's own agent (when the ``precursor`` built-in is enabled) and any
external MCP host that launches::

    python -m precursor.backend.services.mcp.precursor_server

Every tool is gated by a per-section toggle resolved from the DB at call time
(``resolve_mcp_expose``). Nothing is served until the user opts in under
Settings → MCP servers → "Precursor capabilities", because exposing
conversation history / write actions outbound is a deliberate disclosure.

Sections → tools:
- ``topics``       → list_topics, get_topic (its side chats are listed only
                     when ``chats`` is exposed too)
- ``messages``     → list_messages
- ``chats``        → list_chats, get_chat, list_chat_messages
- ``agents``       → list_agents, get_agent
- ``live``         → list_live_sessions, get_live_session
- ``search``       → search (cross-entity; chats/agents/live hits are only
                     included when their own section is also exposed)
- ``iq``           → retrieve (ranked passages + grounding markdown with [^n]
                     citations; each hit gated by its own section)
- ``iq_ask``       → ask (retrieve + one model call → cited answer)
- ``skills``       → list_skills, get_skill
- ``memory``       → list_memories
- ``memory_write`` → store_memory, update_memory (write — edits long-term memory)
- ``agent_state``  → state_list, state_get, state_set, state_delete (read/write —
                     an agent's private scratchpad that survives re-runs)
- ``workflow_state`` → workflow_state_list, workflow_state_get, workflow_state_set,
                     workflow_state_delete (read/write — a pipeline's shared
                     memory, readable by every step and across runs)
- ``notes``       → append_note (write — persists text verbatim, no LLM turn)
- ``post_message`` → post_message (write — runs a full assistant turn)
- ``side_chats``   → create_side_chat (write — starts a chat linked to a topic)
- ``step_plans``   → create_step_plan, list_step_plans, get_step_plan,
                     amend_step_plan, edit_step_plan, run_step_plan, pause_step_plan (write —
                     delegates a long job to Step by step; a model never
                     approves a step)
- ``schedules``    → list_schedules, get_schedule, create_schedule,
                     set_schedule_enabled, run_schedule_now
- ``reminders``    → list_reminders, get_reminder, set_reminder,
                     cancel_reminder (write — one-shot topic reminders)
- ``workspaces``   → list_workspaces, list_workspace_files, read_workspace_file
                     (read — sandboxed to a workspace's working tree)
- ``workspaces_write`` → create_workspace, create_workspace_file,
                     write_workspace_file, create_workspace_folder (write —
                     lands in the working tree only; committing/pushing stays
                     a separate user action)

The two workspace sections are *strict*: first-party agents run this server with
full access, but file writes and workspace creation must still be opted into.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from precursor.backend.config import get_settings

if TYPE_CHECKING:
    from starlette.applications import Starlette

# Importing each group registers its tools on the shared registrar, in this
# order (which is the order ``tools/list`` advertises them).
from precursor.backend.services.mcp.precursor_tools._core import (
    _GATED,
    _MAX_ROWS,
    _STRICT_SECTIONS,
    F,
    _agent_dict,
    _chat_dict,
    _collection_names,
    _exposed_sections,
    _GatedMCPServer,
    _iso,
    _live_dict,
    _message_dict,
    _now,
    _registrar,
    _reminder_dict,
    _schedule_dict,
    _section_enabled,
    _tool_visible,
    _ToolRegistrar,
    _topic_dict,
    _topic_ref,
    mcp,
)
from precursor.backend.services.mcp.precursor_tools.actions import (
    append_note,
    create_side_chat,
    post_message,
)
from precursor.backend.services.mcp.precursor_tools.basic import (
    _basic,
    calculate,
    convert_timezone,
    convert_units,
    count_text,
    date_math,
    generate_uuid,
    get_datetime,
    get_system_info,
    hash_or_encode,
    random_value,
)
from precursor.backend.services.mcp.precursor_tools.conversations import (
    get_agent,
    get_chat,
    get_live_session,
    get_topic,
    list_agents,
    list_chat_messages,
    list_chats,
    list_live_sessions,
    list_messages,
    list_topics,
    precursor_info,
)
from precursor.backend.services.mcp.precursor_tools.knowledge import (
    get_skill,
    list_memories,
    list_skills,
    store_memory,
    update_memory,
)
from precursor.backend.services.mcp.precursor_tools.schedules import (
    _parse_remind_at,
    cancel_reminder,
    create_schedule,
    get_reminder,
    get_schedule,
    list_reminders,
    list_schedules,
    run_schedule_now,
    set_reminder,
    set_schedule_enabled,
)
from precursor.backend.services.mcp.precursor_tools.search import (
    _ACCESSOR_BY_SECTION,
    _iq_gates,
    ask,
    retrieve,
    search,
)
from precursor.backend.services.mcp.precursor_tools.state import (
    _resolve_state_agent,
    _resolve_workflow,
    _self_agent_id,
    state_delete,
    state_get,
    state_list,
    state_set,
    workflow_state_delete,
    workflow_state_get,
    workflow_state_list,
    workflow_state_set,
)
from precursor.backend.services.mcp.precursor_tools.step_plans import (
    _MAX_PLAN_WAIT_SECONDS,
    _app_post,
    _http_detail,
    _launch_run,
    _plan_scope_error,
    _plan_summary,
    _plan_url,
    _source_policy,
    _with_original_request,
    amend_step_plan,
    create_step_plan,
    edit_step_plan,
    get_step_plan,
    list_step_plans,
    pause_step_plan,
    run_step_plan,
)
from precursor.backend.services.mcp.precursor_tools.workspaces import (
    _DEFAULT_MAX_BYTES,
    _MAX_LIST_ENTRIES,
    _load_workspace,
    _workspace_gate,
    create_workspace,
    create_workspace_file,
    create_workspace_folder,
    list_workspace_files,
    list_workspaces,
    read_workspace_file,
    write_workspace_file,
)

__all__ = [
    "LOOPBACK_HOSTS",
    "_ACCESSOR_BY_SECTION",
    "_DEFAULT_MAX_BYTES",
    "_GATED",
    "_MAX_LIST_ENTRIES",
    "_MAX_PLAN_WAIT_SECONDS",
    "_MAX_ROWS",
    "_STRICT_SECTIONS",
    "F",
    "_GatedMCPServer",
    "_ToolRegistrar",
    "_agent_dict",
    "_app_post",
    "_basic",
    "_chat_dict",
    "_collection_names",
    "_exposed_sections",
    "_http_detail",
    "_iq_gates",
    "_iso",
    "_launch_run",
    "_live_dict",
    "_load_workspace",
    "_message_dict",
    "_now",
    "_parse_remind_at",
    "_plan_scope_error",
    "_plan_summary",
    "_plan_url",
    "_registrar",
    "_reminder_dict",
    "_resolve_state_agent",
    "_resolve_workflow",
    "_schedule_dict",
    "_section_enabled",
    "_self_agent_id",
    "_source_policy",
    "_stdio_mcp",
    "_tool_visible",
    "_topic_dict",
    "_topic_ref",
    "_transport_security",
    "_with_original_request",
    "_workspace_gate",
    "amend_step_plan",
    "append_note",
    "ask",
    "build_http_app",
    "build_mcp",
    "calculate",
    "cancel_reminder",
    "convert_timezone",
    "convert_units",
    "count_text",
    "create_schedule",
    "create_side_chat",
    "create_step_plan",
    "create_workspace",
    "create_workspace_file",
    "create_workspace_folder",
    "date_math",
    "edit_step_plan",
    "generate_uuid",
    "get_agent",
    "get_chat",
    "get_datetime",
    "get_live_session",
    "get_reminder",
    "get_schedule",
    "get_skill",
    "get_step_plan",
    "get_system_info",
    "get_topic",
    "hash_or_encode",
    "http_endpoint_url",
    "is_loopback_host",
    "list_agents",
    "list_chat_messages",
    "list_chats",
    "list_live_sessions",
    "list_memories",
    "list_messages",
    "list_reminders",
    "list_schedules",
    "list_skills",
    "list_step_plans",
    "list_topics",
    "list_workspace_files",
    "list_workspaces",
    "main",
    "mcp",
    "pause_step_plan",
    "post_message",
    "precursor_info",
    "random_value",
    "read_workspace_file",
    "retrieve",
    "run_schedule_now",
    "run_step_plan",
    "search",
    "set_reminder",
    "set_schedule_enabled",
    "state_delete",
    "state_get",
    "state_list",
    "state_set",
    "store_memory",
    "update_memory",
    "workflow_state_delete",
    "workflow_state_get",
    "workflow_state_list",
    "workflow_state_set",
    "write_workspace_file",
]


def _transport_security() -> TransportSecuritySettings:
    """Lock the HTTP transport to the app's own loopback host:port.

    DNS-rebinding protection is on by default; the allowlist below is what makes
    the unauthenticated HTTP endpoint safe — it only answers requests whose Host
    header is this instance's localhost bind. Irrelevant to stdio (which has no
    Host header), so it's harmless when the server runs as a subprocess.
    """
    cfg = get_settings()
    port = cfg.port
    return TransportSecuritySettings(
        allowed_hosts=[
            f"127.0.0.1:{port}",
            f"localhost:{port}",
            f"[::1]:{port}",
            "127.0.0.1",
            "localhost",
            "::1",
        ],
        # Host allowlist is the real boundary; local MCP clients send varied
        # (or no) Origin values, so don't reject on Origin.
        allowed_origins=["*"],
    )


def build_mcp() -> MCPServer:
    """Build a fresh MCPServer 'precursor' server with all tools registered."""
    server = _GatedMCPServer("precursor")
    for fn in _registrar.tools:
        server.tool()(fn)
    return server


def build_http_app(server: MCPServer) -> Starlette:
    """Build ``server``'s streamable-HTTP app (and its session manager).

    MCP 2 takes transport settings here rather than on the constructor. The
    FastAPI app reuses the exact ``/mcp`` route so the bare URL works without a
    trailing-slash redirect; see ``main.create_app``.
    """
    return server.streamable_http_app(
        streamable_http_path="/mcp",
        transport_security=_transport_security(),
    )


# Loopback hosts the HTTP transport is allowed to bind to. 0.0.0.0 is *not*
# loopback — an unauthenticated server must never answer on all interfaces.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def is_loopback_host(host: str) -> bool:
    return host in LOOPBACK_HOSTS


def http_endpoint_url() -> str | None:
    """The localhost URL external MCP hosts use, or None when not loopback-bound."""
    cfg = get_settings()
    if not is_loopback_host(cfg.host):
        return None
    return f"http://{cfg.host}:{cfg.port}/mcp"


# Concrete MCPServer instance for the stdio entrypoint. The HTTP transport builds
# its own per-app instance via build_mcp() (session-manager run-once constraint).
_stdio_mcp = build_mcp()


def main() -> None:
    from precursor.backend.logging_config import configure_subprocess_logging

    configure_subprocess_logging()
    _stdio_mcp.run()


if __name__ == "__main__":
    main()
