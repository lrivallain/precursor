"""Tests for the suggested follow-up parser (``split_suggestions``).

Covers the round trip the streaming surfaces rely on: a trailing ``suggest``
fenced block is lifted off the assistant text, the visible content is left
clean, and the options are de-marked, de-duplicated, and capped.
"""

from __future__ import annotations

from precursor.backend.services.suggestions import (
    MAX_SUGGESTIONS,
    split_suggestions,
)


def test_no_block_returns_text_unchanged() -> None:
    text = "Here is a plain answer with no follow-ups."
    clean, items = split_suggestions(text)
    assert clean == text
    assert items == []


def test_empty_text() -> None:
    assert split_suggestions("") == ("", [])


def test_extracts_and_strips_trailing_block() -> None:
    text = (
        "All three tests are failing on the timeout assertion.\n\n"
        "```suggest\n"
        "- Show me the failing tests\n"
        "- Explain the root cause\n"
        "```"
    )
    clean, items = split_suggestions(text)
    assert clean == "All three tests are failing on the timeout assertion."
    assert items == ["Show me the failing tests", "Explain the root cause"]


def test_various_list_markers_are_stripped() -> None:
    text = "Body\n```suggest\n* one\n1. two\n2) three\n+ four\n```"
    _, items = split_suggestions(text)
    assert items == ["one", "two", "three", "four"]


def test_duplicates_removed_order_preserved() -> None:
    text = "Body\n```suggest\n- a\n- b\n- a\n- c\n```"
    _, items = split_suggestions(text)
    assert items == ["a", "b", "c"]


def test_capped_at_max() -> None:
    lines = "\n".join(f"- option {i}" for i in range(MAX_SUGGESTIONS + 3))
    _, items = split_suggestions(f"Body\n```suggest\n{lines}\n```")
    assert len(items) == MAX_SUGGESTIONS


def test_block_with_no_usable_lines_is_dropped() -> None:
    text = "Body text\n```suggest\n\n\n```"
    clean, items = split_suggestions(text)
    assert clean == "Body text"
    assert items == []


def test_only_the_final_block_is_lifted() -> None:
    text = (
        "First I will show a sample:\n"
        "```suggest\nnot a real block, mid-message\n```\n\n"
        "Done.\n"
        "```suggest\n- Real follow-up\n```"
    )
    clean, items = split_suggestions(text)
    assert items == ["Real follow-up"]
    assert clean.endswith("Done.")
    # The earlier fenced block stays in the visible content.
    assert "mid-message" in clean
