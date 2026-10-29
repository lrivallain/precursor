"""Placing definition-check findings in a file's text (for the Files editor)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.schemas.definitions_api import DefinitionIssue
from precursor.backend.services.definitions.positions import with_positions

WORKFLOW = """\
kind: workflow
id: digest
name: Digest
steps:
  - key: fetch
    agent: agents/fetcher.agent.yaml
  - key: brief
    agent: agents/missing.agent.yaml
    context:
      from: fetch
  - agent: agents/third.agent.yaml
"""


def _at(content: str, location: str | None) -> tuple[int | None, ...]:
    issue = DefinitionIssue(
        severity="error", path="w.workflow.yaml", location=location, message="x"
    )
    [placed] = with_positions(content, [issue])
    return placed.line, placed.column, placed.end_line, placed.end_column


def test_a_step_value_is_found_by_its_key() -> None:
    # The value itself is marked: `agents/missing.agent.yaml` on line 8.
    assert _at(WORKFLOW, "steps[brief].agent") == (8, 12, 8, 37)


def test_a_nested_value_is_found() -> None:
    assert _at(WORKFLOW, "steps[brief].context.from") == (10, 13, 10, 18)


def test_a_block_value_is_marked_at_its_key() -> None:
    assert _at(WORKFLOW, "steps[brief].context") == (9, 5, 9, 12)


def test_a_step_without_a_key_is_found_by_index() -> None:
    assert _at(WORKFLOW, "steps[2].agent") == (11, 12, 11, 35)


def test_a_step_is_marked_at_its_key_value() -> None:
    assert _at(WORKFLOW, "steps[brief]") == (7, 10, 7, 15)


def test_a_missing_key_falls_back_to_the_nearest_ancestor() -> None:
    # No `role` in the step: the step itself (its `key: brief`) is marked.
    assert _at(WORKFLOW, "steps[brief].role") == (7, 10, 7, 15)
    # No such step at all: the `steps` key.
    assert _at(WORKFLOW, "steps[nope].agent") == (4, 1, 4, 6)


def test_a_top_level_finding_without_location_marks_the_first_key() -> None:
    assert _at(WORKFLOW, None) == (1, 1, 1, 5)
    assert _at(WORKFLOW, "title") == (1, 1, 1, 5)


def test_a_yaml_error_is_placed_at_its_mark() -> None:
    broken = "kind: agent\nid: a\ntitle: [unclosed\n"
    line, column, end_line, end_column = _at(broken, None)
    assert line == 4 and end_line == 4
    assert end_column == column + 1


def test_a_duplicate_key_is_placed_on_the_second_one() -> None:
    doubled = "kind: agent\nid: a\ntitle: A\ntitle: B\n"
    assert _at(doubled, None)[0] == 4


def test_an_empty_file_is_marked_on_the_first_line() -> None:
    assert _at("", None) == (1, 1, 1, 2)


def test_columns_count_utf16_units_and_crlf_breaks_lines() -> None:
    # "🙂" is two UTF-16 units, as Monaco counts; \r\n is one break.
    content = 'kind: agent\r\nid: a\r\ntitle: "🙂 x"\r\nrole: nope\r\n'
    assert _at(content, "role") == (4, 7, 4, 11)
    assert _at(content, "title") == (3, 8, 3, 14)


def test_an_odd_location_falls_back_instead_of_failing() -> None:
    assert _at(WORKFLOW, "capabilities.mcp_servers.list[str].0") == (1, 1, 1, 5)
    assert _at(WORKFLOW, "steps[brief]]]") == (1, 1, 1, 5)


# --- API ----------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["agent", "workflow"])
def test_the_schema_endpoint_serves_the_published_schema(kind: str) -> None:
    committed = Path(__file__).parents[1] / "docs" / "schemas" / f"{kind}.schema.json"
    with TestClient(create_app()) as client:
        resp = client.get(f"/api/definitions/schema/{kind}")
    assert resp.status_code == 200
    assert resp.json() == json.loads(committed.read_text(encoding="utf-8"))


def test_the_schema_endpoint_rejects_an_unknown_kind() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/api/definitions/schema/skill").status_code == 422
