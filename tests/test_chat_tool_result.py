"""Tests for ``_format_tool_result`` — how MCP tool results are stringified.

Regression coverage for the hosted WorkIQ endpoint, whose reads come back with
an empty ``content`` list and the payload in ``structuredContent``. Before the
fix those results rendered as ``(empty result)``, so the model never saw the
message IDs it needed to move mail or set categories.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from precursor.backend.services.turn_engine import format_tool_result


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(text=text)


def test_text_content_blocks_are_joined() -> None:
    payload = SimpleNamespace(
        content=[_text_block("first"), _text_block("second")],
        structuredContent=None,
    )
    assert format_tool_result(payload) == "first\n\nsecond"


def test_structured_content_used_when_no_text_blocks() -> None:
    structured = {"results": [{"data": {"id": "abc", "subject": "hi"}}]}
    payload = SimpleNamespace(content=[], structuredContent=structured)
    assert json.loads(format_tool_result(payload)) == structured


def test_text_blocks_take_precedence_over_structured_content() -> None:
    payload = SimpleNamespace(
        content=[_text_block("summary")],
        structuredContent={"ignored": True},
    )
    assert format_tool_result(payload) == "summary"


def test_empty_result_when_no_content_and_no_structured() -> None:
    payload = SimpleNamespace(content=[], structuredContent=None)
    assert format_tool_result(payload) == "(empty result)"


def test_none_content_falls_back_to_payload_dump() -> None:
    payload = SimpleNamespace(content=None)
    # No ``content`` at all: the whole payload is dumped via ``default=str``.
    assert "content" in format_tool_result(payload)
