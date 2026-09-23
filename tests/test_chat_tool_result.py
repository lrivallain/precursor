"""Tests for ``_format_tool_result`` — how MCP tool results are stringified.

Regression coverage for the hosted WorkIQ endpoint, whose reads come back with
an empty ``content`` list and the payload in ``structured_content``. Before the
fix those results rendered as ``(empty result)``, so the model never saw the
message IDs it needed to move mail or set categories.

The payloads are real SDK ``CallToolResult`` objects rather than look-alikes:
MCP 2 renamed ``structuredContent`` to ``structured_content``, and a hand-built
double spelled the old way kept these tests green while the real field went
unread.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from mcp.types import CallToolResult, ImageContent, TextContent

from precursor.backend.services.turn_engine import format_tool_result


def _text_block(text: str) -> TextContent:
    return TextContent(type="text", text=text)


def test_text_content_blocks_are_joined() -> None:
    payload = CallToolResult(content=[_text_block("first"), _text_block("second")])
    assert format_tool_result(payload) == "first\n\nsecond"


def test_structured_content_used_when_no_text_blocks() -> None:
    structured = {"results": [{"data": {"id": "abc", "subject": "hi"}}]}
    payload = CallToolResult(content=[], structured_content=structured)
    assert json.loads(format_tool_result(payload)) == structured


def test_text_blocks_take_precedence_over_structured_content() -> None:
    payload = CallToolResult(content=[_text_block("summary")], structured_content={"ignored": True})
    assert format_tool_result(payload) == "summary"


def test_empty_result_when_no_content_and_no_structured() -> None:
    payload = CallToolResult(content=[])
    assert format_tool_result(payload) == "(empty result)"


def test_non_text_blocks_keep_the_mcp_wire_spelling() -> None:
    """A non-text block is dumped with its protocol field names (``mimeType``)."""
    image = ImageContent(type="image", data="aGk=", mime_type="image/png")
    dumped = json.loads(format_tool_result(CallToolResult(content=[image])))
    assert dumped["mimeType"] == "image/png"
    assert "mime_type" not in dumped


def test_none_content_falls_back_to_payload_dump() -> None:
    payload = SimpleNamespace(content=None)
    # No ``content`` at all: the whole payload is dumped via ``default=str``.
    assert "content" in format_tool_result(payload)
