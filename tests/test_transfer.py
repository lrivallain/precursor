"""YAML export/import of agents and workflows.

Covers the contract that makes a file shareable: what travels (the definition),
what deliberately doesn't (runtime state, secrets, an armed schedule), and the
three ways an incoming agent that collides with an existing one can be resolved.
"""

from __future__ import annotations

import json

import yaml
from fastapi.testclient import TestClient

from precursor.backend.main import create_app


async def _ensure_schema() -> None:
    from precursor.backend.db import init_db

    await init_db()


async def _set_agents_enabled(enabled: bool) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AppSetting

    async with SessionLocal() as session:
        row = await session.get(AppSetting, "agents_enabled")
        encoded = json.dumps(enabled)
        if row is None:
            session.add(AppSetting(key="agents_enabled", value=encoded))
        else:
            row.value = encoded
        await session.commit()


async def _make_agent(**kwargs) -> int:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    async with SessionLocal() as session:
        agent = AgentSession(
            title=kwargs.pop("title", "Agent"),
            task_prompt=kwargs.pop("task_prompt", "do the thing"),
            status=kwargs.pop("status", "waiting"),
            **kwargs,
        )
        session.add(agent)
        await session.commit()
        return agent.id


async def _make_workflow(name: str, agent_ids: list[int]) -> int:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Workflow, WorkflowStep

    async with SessionLocal() as session:
        workflow = Workflow(name=name, status="idle", description="pipeline")
        session.add(workflow)
        await session.flush()
        for pos, agent_id in enumerate(agent_ids):
            session.add(WorkflowStep(workflow_id=workflow.id, agent_id=agent_id, position=pos))
        await session.commit()
        return workflow.id


async def _agent_titles() -> list[str]:
    from sqlalchemy import select

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    async with SessionLocal() as session:
        rows = (
            (await session.execute(select(AgentSession).where(AgentSession.inline.is_(False))))
            .scalars()
            .all()
        )
        return sorted(r.title for r in rows)


# --- Export -----------------------------------------------------------------


async def test_workflow_export_carries_its_external_agents() -> None:
    """A pipeline that arrives without its agents isn't runnable, so they travel."""
    await _ensure_schema()
    await _set_agents_enabled(True)
    a = await _make_agent(title="Researcher", task_prompt="research the topic", model="gpt-5")
    b = await _make_agent(title="Writer", task_prompt="write it up")
    wf_id = await _make_workflow("Research pipeline", [a, b])

    with TestClient(create_app()) as client:
        resp = client.get(f"/api/transfer/workflows/{wf_id}")
        assert resp.status_code == 200, resp.text
        assert "attachment" in resp.headers["content-disposition"]
        doc = yaml.safe_load(resp.text)

    assert doc["kind"] == "workflow"
    assert doc["workflow"]["name"] == "Research pipeline"
    assert [a["title"] for a in doc["agents"]] == ["Researcher", "Writer"]
    assert doc["agents"][0]["task"] == "research the topic"
    assert doc["agents"][0]["model"] == "gpt-5"
    # Steps address agents by index within the file, never by a local database id.
    assert [s["agent"] for s in doc["workflow"]["steps"]] == [0, 1]


async def test_export_omits_runtime_state_and_secrets() -> None:
    """The file describes what to run, never what happened — nor how to trigger it."""
    await _ensure_schema()
    await _set_agents_enabled(True)
    a = await _make_agent(
        title="Stateful", status="completed", result_summary="done", total_input_tokens=999
    )
    wf_id = await _make_workflow("Has state", [a])

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Workflow

    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None
        wf.webhook_token = "super-secret-token"
        wf.run_count = 7
        wf.status = "completed"
        await session.commit()

    with TestClient(create_app()) as client:
        raw = client.get(f"/api/transfer/workflows/{wf_id}").text

    assert "super-secret-token" not in raw
    for leaked in ("run_count", "result_summary", "webhook_token", "copilot_session_id", "status"):
        assert leaked not in raw, f"{leaked} leaked into the export"


async def test_inline_step_agents_travel_embedded() -> None:
    """A step's private vessel is part of the pipeline, so it exports with it."""
    await _ensure_schema()
    await _set_agents_enabled(True)
    with TestClient(create_app()) as client:
        created = client.post(
            "/api/workflows",
            json={
                "name": "Inline pipeline",
                "steps": [{"task": "summarise the input", "name": "Summarise"}],
            },
        )
        assert created.status_code == 201, created.text
        wf_id = created.json()["id"]
        doc = yaml.safe_load(client.get(f"/api/transfer/workflows/{wf_id}").text)

    assert len(doc["agents"]) == 1
    assert doc["agents"][0]["inline"] is True
    assert doc["agents"][0]["task"] == "summarise the input"
    # A private vessel has no shared identity to round-trip.
    assert "export_id" not in doc["agents"][0]


async def test_agent_export_refuses_inline_vessels() -> None:
    """An inline agent isn't a thing you can own on its own."""
    await _ensure_schema()
    await _set_agents_enabled(True)
    inline_id = await _make_agent(title="Vessel", inline=True)

    with TestClient(create_app()) as client:
        resp = client.get(f"/api/transfer/agents/{inline_id}")
    assert resp.status_code == 400
    assert "workflow" in resp.json()["detail"].lower()


# --- Import: conflict detection --------------------------------------------


async def test_preview_reports_agent_conflict_without_writing() -> None:
    await _ensure_schema()
    await _set_agents_enabled(True)
    existing = await _make_agent(title="Reviewer", task_prompt="original prompt")
    await _make_workflow("Uses reviewer", [existing])

    doc = yaml.safe_dump(
        {
            "format": 1,
            "kind": "workflow",
            "agents": [{"title": "Reviewer", "task": "incoming prompt"}],
            "workflow": {"name": "Imported", "steps": [{"agent": 0}]},
        }
    )

    before = await _agent_titles()
    with TestClient(create_app()) as client:
        preview = client.post("/api/transfer/preview", json={"content": doc})
    assert preview.status_code == 200, preview.text
    body = preview.json()

    conflict = next(c for c in body["conflicts"] if c["kind"] == "agent")
    assert conflict["name"] == "Reviewer"
    assert conflict["existing_id"] == existing
    assert conflict["same_object"] is False
    # The blast radius of a replace is surfaced up front.
    assert conflict["workflow_count"] == 1
    assert set(conflict["allowed"]) == {"replace", "create", "link"}
    # A bare name match is far too weak a signal to overwrite someone's prompt.
    assert conflict["default"] == "link"
    # Preview is read-only.
    assert await _agent_titles() == before


async def test_preview_recognises_a_round_tripped_export() -> None:
    """Re-importing a file this install produced targets the very same object."""
    await _ensure_schema()
    await _set_agents_enabled(True)
    agent_id = await _make_agent(title="Round tripper", task_prompt="v1")

    with TestClient(create_app()) as client:
        exported = client.get(f"/api/transfer/agents/{agent_id}").text
        body = client.post("/api/transfer/preview", json={"content": exported}).json()

    conflict = next(c for c in body["conflicts"] if c["kind"] == "agent")
    assert conflict["same_object"] is True
    # Provably the same agent, so updating it in place is the safe default.
    assert conflict["default"] == "replace"


# --- Import: the three resolutions -----------------------------------------


def _collide_doc(title: str, task: str, wf_name: str) -> str:
    return yaml.safe_dump(
        {
            "format": 1,
            "kind": "workflow",
            "agents": [{"title": title, "task": task}],
            "workflow": {"name": wf_name, "steps": [{"agent": 0}]},
        }
    )


async def _agent_prompt(agent_id: int) -> str:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        return agent.task_prompt


async def _step_agent_ids(workflow_id: int) -> list[int | None]:
    from sqlalchemy import select

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import WorkflowStep

    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(WorkflowStep)
                    .where(WorkflowStep.workflow_id == workflow_id)
                    .order_by(WorkflowStep.position)
                )
            )
            .scalars()
            .all()
        )
        return [r.agent_id for r in rows]


async def test_import_link_reuses_the_existing_agent_untouched() -> None:
    await _ensure_schema()
    await _set_agents_enabled(True)
    existing = await _make_agent(title="Linker", task_prompt="keep me")

    with TestClient(create_app()) as client:
        result = client.post(
            "/api/transfer/import",
            json={
                "content": _collide_doc("Linker", "overwrite me", "Link pipeline"),
                "resolutions": [{"kind": "agent", "index": 0, "action": "link"}],
            },
        )
    assert result.status_code == 200, result.text
    body = result.json()

    assert body["linked_agent_ids"] == [existing]
    assert body["created_agent_ids"] == []
    # Linking means the agent you already have, exactly as you had it.
    assert await _agent_prompt(existing) == "keep me"
    assert await _step_agent_ids(body["workflow_id"]) == [existing]


async def test_import_replace_edits_in_place_so_other_pipelines_follow() -> None:
    await _ensure_schema()
    await _set_agents_enabled(True)
    existing = await _make_agent(title="Replacer", task_prompt="old prompt")
    other_wf = await _make_workflow("Pre-existing pipeline", [existing])

    with TestClient(create_app()) as client:
        body = client.post(
            "/api/transfer/import",
            json={
                "content": _collide_doc("Replacer", "new prompt", "Replace pipeline"),
                "resolutions": [{"kind": "agent", "index": 0, "action": "replace"}],
            },
        ).json()

    assert body["replaced_agent_ids"] == [existing]
    assert await _agent_prompt(existing) == "new prompt"
    # Keeping the row id is the whole point: the other pipeline picks the new
    # definition up rather than silently drifting from it.
    assert await _step_agent_ids(other_wf) == [existing]


async def test_import_create_leaves_the_original_alone() -> None:
    await _ensure_schema()
    await _set_agents_enabled(True)
    existing = await _make_agent(title="Creator", task_prompt="original")

    with TestClient(create_app()) as client:
        body = client.post(
            "/api/transfer/import",
            json={
                "content": _collide_doc("Creator", "the copy", "Create pipeline"),
                "resolutions": [{"kind": "agent", "index": 0, "action": "create"}],
            },
        ).json()

    (created,) = body["created_agent_ids"]
    assert created != existing
    assert await _agent_prompt(existing) == "original"
    assert await _agent_prompt(created) == "the copy"
    # The copy is disambiguated so both are legible in the roster.
    assert "Creator (2)" in await _agent_titles()
    assert await _step_agent_ids(body["workflow_id"]) == [created]


async def test_import_without_resolutions_never_overwrites_a_name_match() -> None:
    """A scripted import with no UI must still be non-destructive."""
    await _ensure_schema()
    await _set_agents_enabled(True)
    existing = await _make_agent(title="Unattended", task_prompt="precious")

    with TestClient(create_app()) as client:
        body = client.post(
            "/api/transfer/import",
            json={"content": _collide_doc("Unattended", "clobber", "Unattended pipeline")},
        ).json()

    assert body["linked_agent_ids"] == [existing]
    assert await _agent_prompt(existing) == "precious"


# --- Import: round-trip and hygiene ----------------------------------------


async def test_workflow_round_trips_through_yaml() -> None:
    await _ensure_schema()
    await _set_agents_enabled(True)
    with TestClient(create_app()) as client:
        created = client.post(
            "/api/workflows",
            json={
                "name": "Round trip",
                "description": "three steps",
                "max_loops": 5,
                "steps": [
                    {"task": "gather", "name": "Gather", "reusable": True, "title": "Gatherer"},
                    {"task": "check it", "name": "Check", "kind": "gate", "on_fail_position": 0},
                    {"name": "Sign off", "kind": "approval", "on_reject": "stop"},
                ],
            },
        )
        assert created.status_code == 201, created.text
        exported = client.get(f"/api/transfer/workflows/{created.json()['id']}").text

        # Import as a fresh copy so the original is untouched.
        result = client.post(
            "/api/transfer/import",
            json={
                "content": exported,
                "resolutions": [
                    {"kind": "workflow", "index": None, "action": "create"},
                    {"kind": "agent", "index": 0, "action": "create"},
                ],
            },
        )
        assert result.status_code == 200, result.text
        imported = client.get(f"/api/workflows/{result.json()['workflow_id']}").json()

    assert imported["name"] == "Round trip (2)"
    assert imported["description"] == "three steps"
    assert imported["max_loops"] == 5
    assert [s["kind"] for s in imported["steps"]] == ["task", "gate", "approval"]
    assert imported["steps"][1]["on_fail_position"] == 0
    assert imported["steps"][2]["on_reject"] == "stop"
    # An approval checkpoint runs no agent.
    assert imported["steps"][2]["agent_id"] is None


async def test_imported_schedule_arrives_paused() -> None:
    """A shared file describes a cadence; it doesn't get to start firing."""
    await _ensure_schema()
    await _set_agents_enabled(True)
    doc = yaml.safe_dump(
        {
            "format": 1,
            "kind": "workflow",
            "agents": [{"title": "Nightly worker", "task": "sweep"}],
            "workflow": {
                "name": "Nightly",
                "steps": [{"agent": 0}],
                "schedule": {"run_at_minute": 120, "timezone": "Europe/Paris"},
            },
        }
    )

    with TestClient(create_app()) as client:
        body = client.post("/api/transfer/import", json={"content": doc}).json()
        workflow = client.get(f"/api/workflows/{body['workflow_id']}").json()

    assert workflow["schedule_enabled"] is False
    assert workflow["run_at_minute"] == 120
    assert workflow["timezone"] == "Europe/Paris"
    assert workflow["next_run_at"] is None
    assert any(w["code"] == "schedule" for w in body["warnings"])


async def test_imported_role_is_matched_by_name_not_duplicated() -> None:
    await _ensure_schema()
    await _set_agents_enabled(True)
    doc = yaml.safe_dump(
        {
            "format": 1,
            "kind": "agent",
            "agents": [
                {
                    "title": "Persona user",
                    "task": "speak",
                    "role": {"name": "Terse editor", "system_prompt": "Be brief."},
                }
            ],
        }
    )

    with TestClient(create_app()) as client:
        first = client.post("/api/transfer/import", json={"content": doc})
        assert first.status_code == 200, first.text
        second = client.post(
            "/api/transfer/import",
            json={
                "content": doc,
                "resolutions": [{"kind": "agent", "index": 0, "action": "create"}],
            },
        )
        assert second.status_code == 200, second.text
        roles = [r["name"] for r in client.get("/api/roles").json()]

    # Importing the same persona twice converges on one role.
    assert roles.count("Terse editor") == 1


async def test_replacing_a_workflow_rewires_its_steps() -> None:
    await _ensure_schema()
    await _set_agents_enabled(True)
    a = await _make_agent(title="Step one")
    b = await _make_agent(title="Step two")
    wf_id = await _make_workflow("Replace me", [a, b])

    doc = yaml.safe_dump(
        {
            "format": 1,
            "kind": "workflow",
            "agents": [{"title": "Step one", "task": "only step"}],
            "workflow": {"name": "Replace me", "steps": [{"agent": 0}]},
        }
    )

    with TestClient(create_app()) as client:
        body = client.post(
            "/api/transfer/import",
            json={
                "content": doc,
                "resolutions": [
                    {"kind": "workflow", "index": None, "action": "replace"},
                    {"kind": "agent", "index": 0, "action": "link"},
                ],
            },
        ).json()

    assert body["workflow_id"] == wf_id
    assert await _step_agent_ids(wf_id) == [a]


# --- Bad input --------------------------------------------------------------


async def test_import_rejects_malformed_documents() -> None:
    await _ensure_schema()
    await _set_agents_enabled(True)
    cases = [
        ("just a string", "top level"),
        ("format: 999\nkind: agent\nagents: []\n", "newer version"),
        (
            yaml.safe_dump(
                {
                    "format": 1,
                    "kind": "workflow",
                    "agents": [],
                    "workflow": {"name": "Dangling", "steps": [{"agent": 3}]},
                }
            ),
            "doesn't define",
        ),
        ("kind: agent\nagents: []\n", "exactly one agent"),
    ]
    with TestClient(create_app()) as client:
        for content, expected in cases:
            resp = client.post("/api/transfer/preview", json={"content": content})
            assert resp.status_code == 400, content
            assert expected in resp.json()["detail"], resp.json()["detail"]


async def test_transfer_requires_agents_mode() -> None:
    await _ensure_schema()
    await _set_agents_enabled(False)
    with TestClient(create_app()) as client:
        resp = client.post("/api/transfer/preview", json={"content": "kind: agent\nagents: []\n"})
    assert resp.status_code == 409
    await _set_agents_enabled(False)
