"""Workflow state tests — the pipeline's shared memory and step placeholders.

Covers what makes workflow state a *different* surface from agent state: it is
keyed to the pipeline (so two workflows sharing a reusable agent don't collide),
it outlives a run, and steps consume it through ``{{state.<key>}}`` placeholders
resolved before the agent ever sees its instructions.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import Workflow
from precursor.backend.models.workflow_state import WORKFLOW_STATE_MAX_KEYS
from precursor.backend.schemas.workflow_state import WorkflowStateWrite
from precursor.backend.services import workflow_state as state_service
from precursor.backend.services.workflow_state import (
    UNSET_PLACEHOLDER,
    has_placeholders,
    render_placeholders,
)

_created: list[int] = []


@pytest.fixture(autouse=True)
async def _cleanup_workflows():
    """Drop the workflows a test created — the suite shares one SQLite file."""
    yield
    async with SessionLocal() as session:
        for workflow_id in _created:
            workflow = await session.get(Workflow, workflow_id)
            if workflow is not None:
                await session.delete(workflow)
        await session.commit()
    _created.clear()


async def _make_workflow(name: str = "state pipeline") -> int:
    # Lifespan runs init_db (alembic upgrade head) before we touch the tables.
    with TestClient(create_app()):
        pass
    async with SessionLocal() as session:
        workflow = Workflow(name=name)
        session.add(workflow)
        await session.commit()
        await session.refresh(workflow)
        _created.append(workflow.id)
        return workflow.id


# ------------------------------------------------------------------- the store


async def test_set_state_upserts_by_key() -> None:
    workflow_id = await _make_workflow()
    async with SessionLocal() as session:
        first, created = await state_service.set_state(
            session, workflow_id, WorkflowStateWrite(key="cursor", value="1")
        )
        assert created is True

        second, created_again = await state_service.set_state(
            session, workflow_id, WorkflowStateWrite(key="cursor", value="2")
        )
        assert created_again is False
        assert second.id == first.id and second.value == "2"
        assert len(await state_service.list_states(session, workflow_id)) == 1


async def test_state_is_scoped_per_workflow() -> None:
    """The whole reason this isn't agent state: two pipelines can share an agent."""
    a, b = await _make_workflow("a"), await _make_workflow("b")
    async with SessionLocal() as session:
        await state_service.set_state(session, a, WorkflowStateWrite(key="cursor", value="a"))
        await state_service.set_state(session, b, WorkflowStateWrite(key="cursor", value="b"))

        got_a = await state_service.get_state(session, a, "cursor")
        got_b = await state_service.get_state(session, b, "cursor")
        assert got_a is not None and got_a.value == "a"
        assert got_b is not None and got_b.value == "b"


async def test_key_index_omits_bodies() -> None:
    workflow_id = await _make_workflow()
    body = "y" * 4_000
    async with SessionLocal() as session:
        await state_service.set_state(
            session, workflow_id, WorkflowStateWrite(key="big", value=body)
        )
        entries = await state_service.list_state_keys(session, workflow_id)
        assert [(e.key, e.size) for e in entries] == [("big", 4_000)]

        prompt = await state_service.build_state_index_prompt(session, workflow_id)
        assert prompt is not None and "`big`" in prompt
        assert body not in prompt


async def test_key_cap_rejects_new_keys_but_allows_overwrite() -> None:
    workflow_id = await _make_workflow()
    async with SessionLocal() as session:
        for i in range(WORKFLOW_STATE_MAX_KEYS):
            await state_service.set_state(
                session, workflow_id, WorkflowStateWrite(key=f"k{i}", value="v")
            )
        with pytest.raises(ValueError, match="limited to"):
            await state_service.set_state(
                session, workflow_id, WorkflowStateWrite(key="overflow", value="v")
            )
        _row, created = await state_service.set_state(
            session, workflow_id, WorkflowStateWrite(key="k0", value="updated")
        )
        assert created is False


async def test_state_is_deleted_with_its_workflow() -> None:
    workflow_id = await _make_workflow()
    async with SessionLocal() as session:
        await state_service.set_state(session, workflow_id, WorkflowStateWrite(key="c", value="1"))

    async with SessionLocal() as session:
        workflow = await session.get(Workflow, workflow_id)
        assert workflow is not None
        await session.delete(workflow)
        await session.commit()

    async with SessionLocal() as session:
        assert await state_service.list_states(session, workflow_id) == []


# ------------------------------------------------------------- the placeholders


def test_render_substitutes_each_supported_expression() -> None:
    rendered = render_placeholders(
        "state={{state.cursor}} input={{run.input}} out={{step.2.output}}",
        state={"cursor": "abc"},
        run_input="the brief",
        step_outputs={2: "step two said this"},
    )
    assert rendered == "state=abc input=the brief out=step two said this"


def test_missing_values_fall_back_to_the_default() -> None:
    rendered = render_placeholders("since {{state.last_run | the beginning of time}}", state={})
    assert rendered == "since the beginning of time"


def test_missing_values_without_a_default_are_marked_unset() -> None:
    """An honest marker beats a silent blank — the model can reason about it."""
    assert render_placeholders("since {{state.last_run}}", state={}) == f"since {UNSET_PLACEHOLDER}"


def test_empty_values_are_treated_as_missing() -> None:
    rendered = render_placeholders("x={{state.k | fallback}}", state={"k": "   "})
    assert rendered == "x=fallback"


def test_unknown_expressions_are_left_alone() -> None:
    """Braces belonging to prose or another tool's templating must survive."""
    text = "keep {{mustache.thing}} and {{ not_ours }} intact"
    assert render_placeholders(text, state={"thing": "x"}) == text


def test_has_placeholders_only_matches_ours() -> None:
    assert has_placeholders("hello {{state.k}}") is True
    assert has_placeholders("hello {{run.input}}") is True
    assert has_placeholders("hello {{step.0.output}}") is True
    assert has_placeholders("hello {{something.else}}") is False
    assert has_placeholders(None) is False


async def test_render_step_instructions_reads_live_state() -> None:
    workflow_id = await _make_workflow()
    async with SessionLocal() as session:
        await state_service.set_state(
            session, workflow_id, WorkflowStateWrite(key="audience", value="release engineers")
        )
        rendered = await state_service.render_step_instructions(
            session, workflow_id, None, "Write for {{state.audience | a general audience}}."
        )
    assert rendered == "Write for release engineers."


async def test_render_step_instructions_uses_defaults_on_a_first_run() -> None:
    workflow_id = await _make_workflow()
    async with SessionLocal() as session:
        rendered = await state_service.render_step_instructions(
            session, workflow_id, None, "Since {{state.cursor | the beginning}}."
        )
    assert rendered == "Since the beginning."


# -------------------------------------------------------------------- the API


async def test_http_state_roundtrip() -> None:
    workflow_id = await _make_workflow()
    app = create_app()
    with TestClient(app) as client:
        assert client.get(f"/api/workflows/{workflow_id}/state").json() == []

        written = client.put(
            f"/api/workflows/{workflow_id}/state", json={"key": "cursor", "value": "42"}
        )
        assert written.status_code == 200 and written.json()["key"] == "cursor"

        listed = client.get(f"/api/workflows/{workflow_id}/state").json()
        assert [r["key"] for r in listed] == ["cursor"]

        assert client.delete(f"/api/workflows/{workflow_id}/state/cursor").status_code == 204
        assert client.delete(f"/api/workflows/{workflow_id}/state/cursor").status_code == 404


async def test_http_clear_wipes_pipeline_state() -> None:
    workflow_id = await _make_workflow()
    app = create_app()
    with TestClient(app) as client:
        client.put(f"/api/workflows/{workflow_id}/state", json={"key": "a", "value": "1"})
        client.put(f"/api/workflows/{workflow_id}/state", json={"key": "b", "value": "2"})
        assert client.delete(f"/api/workflows/{workflow_id}/state").status_code == 204
        assert client.get(f"/api/workflows/{workflow_id}/state").json() == []


async def test_mcp_tools_require_a_workflow_without_context(monkeypatch) -> None:
    from precursor.backend.services.mcp import precursor_server as ps

    monkeypatch.setenv("PRECURSOR_MCP_FULL_ACCESS", "1")
    monkeypatch.delenv("PRECURSOR_AGENT_ID", raising=False)

    result = await ps.workflow_state_list()
    assert "No workflow in context" in result["error"]


async def test_mcp_tools_accept_an_explicit_workflow(monkeypatch) -> None:
    from precursor.backend.services.mcp import precursor_server as ps

    monkeypatch.setenv("PRECURSOR_MCP_FULL_ACCESS", "1")
    workflow_id = await _make_workflow()

    saved = await ps.workflow_state_set(key="cursor", value="42", workflow_id=workflow_id)
    assert saved == {"workflow_id": workflow_id, "key": "cursor", "created": True, "size": 2}

    read = await ps.workflow_state_get(key="cursor", workflow_id=workflow_id)
    assert read["found"] is True and read["value"] == "42"

    listed = await ps.workflow_state_list(workflow_id=workflow_id)
    assert [k["key"] for k in listed["keys"]] == ["cursor"]
    assert "value" not in listed["keys"][0]

    missing = await ps.workflow_state_get(key="nope", workflow_id=workflow_id)
    assert missing["found"] is False and "error" not in missing

    assert (await ps.workflow_state_delete(key="cursor", workflow_id=workflow_id))[
        "deleted"
    ] is True


async def test_mcp_workflow_state_is_gated_for_external_clients(monkeypatch) -> None:
    from precursor.backend.services.mcp import precursor_server as ps

    monkeypatch.delenv("PRECURSOR_MCP_FULL_ACCESS", raising=False)
    workflow_id = await _make_workflow()

    result = await ps.workflow_state_list(workflow_id=workflow_id)
    assert "not exposed" in result["error"]
