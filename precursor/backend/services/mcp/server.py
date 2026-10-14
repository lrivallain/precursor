"""Outbound MCP — Precursor as an MCP server exposing its own capabilities.

The working implementation lives in
:mod:`precursor.backend.services.mcp.precursor_server` (an MCPServer stdio server
registered as the built-in ``precursor`` entry, see ``mcp/client.py``). External
hosts connect by launching::

    python -m precursor.backend.services.mcp.precursor_server

This module stays as the lightweight descriptor used by ``GET
/api/mcp/server/info`` to advertise the surface + which capability sections
exist (each gated at call time by the ``mcp_expose`` setting).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from precursor.backend.services.app_settings import MCP_EXPOSE_SECTIONS

# Advertised tools grouped by the ``mcp_expose`` section that gates them.
_TOOLS_BY_SECTION: dict[str, list[str]] = {
    "topics": ["list_topics", "get_topic"],
    "messages": ["list_messages"],
    "chats": ["list_chats", "get_chat", "list_chat_messages"],
    "agents": ["list_agents", "get_agent"],
    "live": ["list_live_sessions", "get_live_session"],
    "search": ["search"],
    "iq": ["retrieve"],
    "iq_ask": ["ask"],
    "skills": ["list_skills", "get_skill"],
    "memory": ["list_memories"],
    "memory_write": ["store_memory", "update_memory"],
    "agent_state": ["state_list", "state_get", "state_set", "state_delete"],
    "workflow_state": [
        "workflow_state_list",
        "workflow_state_get",
        "workflow_state_set",
        "workflow_state_delete",
    ],
    "notes": ["append_note"],
    "side_chats": ["create_side_chat"],
    "post_message": ["post_message"],
    "schedules": [
        "list_schedules",
        "get_schedule",
        "create_schedule",
        "set_schedule_enabled",
        "run_schedule_now",
    ],
    "reminders": [
        "list_reminders",
        "get_reminder",
        "set_reminder",
        "cancel_reminder",
    ],
    "workspaces": ["list_workspaces", "list_workspace_files", "read_workspace_file"],
    "workspaces_write": [
        "create_workspace",
        "create_workspace_file",
        "write_workspace_file",
        "create_workspace_folder",
    ],
}

# Tool name -> the section whose toggle gates it. ``precursor_info`` is absent on
# purpose: it carries no data and is always advertised.
TOOL_SECTIONS: dict[str, str] = {
    name: section for section, names in _TOOLS_BY_SECTION.items() for name in names
}


@dataclass(slots=True)
class ToolSpec:
    name: str
    section: str


class PrecursorMCPServer:
    """Declarative description of the MCP surface Precursor can expose."""

    def __init__(self) -> None:
        self.transport = "stdio"
        self.entrypoint = "python -m precursor.backend.services.mcp.precursor_server"
        self.sections: tuple[str, ...] = MCP_EXPOSE_SECTIONS
        self.tools: list[ToolSpec] = [
            ToolSpec(name=name, section=section)
            for section, names in _TOOLS_BY_SECTION.items()
            for name in names
        ]

    def describe(self) -> dict[str, Any]:
        from precursor import __version__

        return {
            "name": "precursor",
            "version": __version__,
            "transport": self.transport,
            "entrypoint": self.entrypoint,
            "sections": list(self.sections),
            "tools": [{"name": t.name, "section": t.section} for t in self.tools],
        }


@lru_cache
def get_mcp_server() -> PrecursorMCPServer:
    return PrecursorMCPServer()
