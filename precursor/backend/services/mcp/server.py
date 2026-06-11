"""Outbound MCP — Precursor as an MCP server exposing its conversations.

This is a thin scaffold: it advertises the tools we plan to expose so external
clients (CLI agents, IDE extensions) can introspect the surface. Wiring it to
the actual MCP transport (stdio / SSE / streamable-http) is the next step and
lives in ``precursor.backend.__main__`` (or a dedicated CLI entrypoint).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


class PrecursorMCPServer:
    """Declarative description of the MCP surface Precursor exposes."""

    def __init__(self) -> None:
        self.tools: list[ToolSpec] = [
            ToolSpec(
                name="list_topics",
                description="List Precursor topics, optionally filtered by a query string.",
                input_schema={
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                },
            ),
            ToolSpec(
                name="get_topic",
                description="Get a topic and its messages by id.",
                input_schema={
                    "type": "object",
                    "required": ["id"],
                    "properties": {"id": {"type": "integer"}},
                },
            ),
            ToolSpec(
                name="post_message",
                description="Append a message to a topic and (optionally) stream an assistant reply.",
                input_schema={
                    "type": "object",
                    "required": ["topic_id", "content"],
                    "properties": {
                        "topic_id": {"type": "integer"},
                        "content": {"type": "string"},
                    },
                },
            ),
        ]

    def describe(self) -> dict[str, Any]:
        return {
            "name": "precursor",
            "version": "0.1.0",
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.input_schema,
                }
                for t in self.tools
            ],
        }


@lru_cache
def get_mcp_server() -> PrecursorMCPServer:
    return PrecursorMCPServer()
