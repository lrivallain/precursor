"""Files mode: agents and workflows declared by their definition files.

Roadmap step 4 (docs/definitions.md). With ``definitions_source = "files"`` a
row linked to a file is projected from it on load, the database columns are
left alone, the runtime refuses a broken file, and a workflow's step rows are
reconciled with the file's step list whenever it isn't mid-run.
"""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from precursor.backend.main import create_app
from tests.definitions_support import mark_database, restore_database

_created_workflows: list[int] = []


class _FakeManager:
    """Stands in for the agents runtime: records which agents a step starts."""

    def __init__(self) -> None:
        self.started: list[int] = []

    def start_task(self, agent_id: int, extra_context: str | None = None, *, run_id=None):  # type: ignore[no-untyped-def]
        self.started.append(agent_id)
        return (agent_id, extra_context)

    def cancel(self, agent_id: int, *, run_id=None):  # type: ignore[no-untyped-def]
        return ("cancel", agent_id)

    def enqueue(self, item) -> None:  # type: ignore[no-untyped-def]
        pass


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture
async def files_mode(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Path]:
    from precursor.backend.config import get_settings
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AppSetting
    from precursor.backend.services.definitions import overlay

    mark = await mark_database()
    async with SessionLocal() as session:
        row = await session.get(AppSetting, "agents_enabled")
        if row is None:
            session.add(AppSetting(key="agents_enabled", value=json.dumps(True)))
        else:
            row.value = json.dumps(True)
        await session.commit()
        await overlay.refresh_role_cache(session)

    settings = get_settings()
    root = Path(settings.definitions_dir)
    shutil.rmtree(root, ignore_errors=True)
    monkeypatch.setattr(settings, "definitions_source", "files")
    overlay.invalidate()
    yield root
    overlay.invalidate()
    overlay._pins.clear()
    shutil.rmtree(root, ignore_errors=True)
    await restore_database(mark)
    _created_workflows.clear()


def _write(root: Path, rel: str, doc: dict[str, Any]) -> None:
    from precursor.backend.services.definitions import overlay

    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    overlay.invalidate()


async def _agent_row(**kwargs: Any) -> int:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    async with SessionLocal() as session:
        agent = AgentSession(status="waiting", **kwargs)
        session.add(agent)
        await session.commit()
        return agent.id


async def _raw(table: str, row_id: int, *cols: str) -> tuple[Any, ...]:
    from precursor.backend.db import SessionLocal

    async with SessionLocal() as session:
        result = await session.execute(
            text(f"select {', '.join(cols)} from {table} where id = :id"), {"id": row_id}
        )
        return tuple(result.one())


# --- Agents -----------------------------------------------------------------


async def test_a_linked_agent_is_declared_by_its_file(files_mode: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    ident = _uid()
    agent_id = await _agent_row(
        title="DB title", task_prompt="db prompt", model="db-model", export_id=ident
    )
    _write(
        files_mode,
        "agents/a.agent.yaml",
        {
            "kind": "agent",
            "id": ident,
            "title": "File title",
            "prompt": "file prompt",
            "model": "file-model",
            "approval_policy": "manual",
            "autonomy": {"enabled": True, "max_steps": 4},
            "capabilities": {"memory": False, "mcp_servers": ["fetch"]},
            "limits": {"token_budget": 99, "max_retries": 2},
        },
    )
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        assert (agent.title, agent.task_prompt, agent.model) == (
            "File title",
            "file prompt",
            "file-model",
        )
        assert (agent.approval_policy, agent.autonomy_enabled, agent.max_steps) == (
            "manual",
            True,
            4,
        )
        assert (agent.use_memory, agent.mcp_servers) == (False, "fetch")
        assert (agent.token_budget, agent.max_retries) == (99, 2)
        # Unrelated state is still the database's.
        agent.status = "completed"
        await session.commit()

    # Projection never writes back: the columns keep their old values, and an
    # unrelated update left them alone too.
    assert await _raw("agent_sessions", agent_id, "title", "task_prompt", "status") == (
        "DB title",
        "db prompt",
        "completed",
    )

    with TestClient(create_app()) as client:
        body = client.get(f"/api/agents/{agent_id}").json()
    assert (body["title"], body["task_prompt"]) == ("File title", "file prompt")


async def test_database_mode_ignores_the_files(
    files_mode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.config import get_settings
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    ident = _uid()
    agent_id = await _agent_row(title="DB title", task_prompt="p", export_id=ident)
    _write(files_mode, "agents/a.agent.yaml", {"kind": "agent", "id": ident, "title": "File"})
    monkeypatch.setattr(get_settings(), "definitions_source", "database")
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None and agent.title == "DB title"


async def test_an_agent_without_a_file_keeps_its_database_values(files_mode: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.definitions import overlay

    agent_id = await _agent_row(title="Only in DB", task_prompt="p", export_id=_uid())
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None and agent.title == "Only in DB"
        assert overlay.definition_error(agent) is None
        assert overlay.provenance(agent) is None


async def test_a_broken_file_is_not_projected_and_refuses_to_run(files_mode: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.definitions import overlay

    ident = _uid()
    agent_id = await _agent_row(title="DB", task_prompt="p", export_id=ident)
    _write(files_mode, "agents/a.agent.yaml", {"kind": "agent", "id": ident, "titel": "x"})
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None and agent.title == "DB"
        problem = overlay.definition_error(agent)
    assert problem is not None and "agents/a.agent.yaml has errors" in problem


async def test_runs_record_the_file_they_ran_from(files_mode: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.definitions import overlay

    ident = _uid()
    agent_id = await _agent_row(title="t", task_prompt="p", export_id=ident)
    _write(files_mode, "agents/a.agent.yaml", {"kind": "agent", "id": ident, "title": "T"})
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        path, digest = overlay.provenance(agent) or ("", "")
    assert path == "agents/a.agent.yaml" and len(digest) == 64


async def test_roles_are_resolved_by_name_including_new_ones(files_mode: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession, Role

    ident, name = _uid(), f"Persona {_uid()}"
    agent_id = await _agent_row(title="t", task_prompt="p", export_id=ident)
    _write(
        files_mode,
        "agents/a.agent.yaml",
        {"kind": "agent", "id": ident, "title": "T", "role": name.upper()},
    )
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None and agent.role_id is None  # not created yet

    # Created after startup: the cache hears about it without a refresh.
    async with SessionLocal() as session:
        role = Role(name=name, system_prompt="")
        session.add(role)
        await session.commit()
        role_id = role.id
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None and agent.role_id == role_id


# --- Workflows --------------------------------------------------------------


async def _exported_workflow() -> dict[str, Any]:
    """A workflow built in the database, then exported — the migration path."""
    from precursor.backend.config import get_settings
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession, Workflow, WorkflowStep
    from precursor.backend.services.definitions.exporter import export_definitions

    tag = _uid()
    async with SessionLocal() as session:
        writer = AgentSession(title=f"Writer {tag}", task_prompt="write", status="waiting")
        vessel = AgentSession(title="Check", task_prompt="judge it", status="waiting", inline=True)
        session.add_all([writer, vessel])
        await session.flush()
        wf = Workflow(name=f"Flow {tag}", status="idle")
        session.add(wf)
        await session.flush()
        session.add_all(
            [
                WorkflowStep(workflow_id=wf.id, position=0, agent_id=writer.id, name="Write"),
                WorkflowStep(
                    workflow_id=wf.id, position=1, agent_id=vessel.id, kind="gate", name="Check"
                ),
            ]
        )
        await session.commit()
        _created_workflows.append(wf.id)
        ids = {"workflow": wf.id, "writer": writer.id, "vessel": vessel.id}

    root = Path(get_settings().definitions_dir)
    async with SessionLocal() as session:
        result = await export_definitions(session, root)
    [entry] = [e for e in result.written if e.kind == "workflow" and e.source_id == ids["workflow"]]
    [writer_entry] = [
        e for e in result.written if e.kind == "agent" and e.source_id == ids["writer"]
    ]
    return {**ids, "path": entry.path, "id": entry.id, "writer_path": writer_entry.path}


async def _steps(workflow_id: int) -> list[Any]:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import WorkflowStep

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(WorkflowStep)
                .where(WorkflowStep.workflow_id == workflow_id)
                .order_by(WorkflowStep.position)
            )
        ).scalars()
        return [
            (r.position, r.definition_ref, r.agent_id, r.kind, r.name, r.instructions) for r in rows
        ]


async def test_existing_steps_are_adopted_keeping_their_agents(files_mode: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    wf = await _exported_workflow()
    with TestClient(create_app()) as client:
        body = client.get(f"/api/workflows/{wf['workflow']}").json()

    steps = await _steps(wf["workflow"])
    # Same agents, so the private agent (and its run history) survives.
    assert [(p, a) for p, _, a, *_ in steps] == [(0, wf["writer"]), (1, wf["vessel"])]
    assert [ref for _, ref, *_ in steps] == [f"{wf['id']}/write", f"{wf['id']}/check"]
    assert [s["name"] for s in body["steps"]] == ["Write", "Check"]
    async with SessionLocal() as session:
        vessel = await session.get(AgentSession, wf["vessel"])
        assert vessel is not None and vessel.definition_ref == f"{wf['id']}/check"


async def test_step_rows_follow_the_file(files_mode: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    wf = await _exported_workflow()
    with TestClient(create_app()) as client:
        client.get(f"/api/workflows/{wf['workflow']}")  # adopt

        new_agent_id = _uid()
        _write(
            files_mode,
            "agents/new-helper.agent.yaml",
            {"kind": "agent", "id": new_agent_id, "title": "Helper", "prompt": "help"},
        )
        doc = yaml.safe_load((files_mode / wf["path"]).read_text(encoding="utf-8"))
        write_step, check_step = doc["steps"]
        check_step["prompt"] = "judge it harder"
        write_step["instructions"] = "from the file"
        doc["steps"] = [
            check_step,  # reordered
            {"key": "help", "agent": "agents/new-helper.agent.yaml"},  # added
            {"key": "sign", "kind": "approval"},  # added; "write" removed
        ]
        _write(files_mode, wf["path"], doc)

        body = client.get(f"/api/workflows/{wf['workflow']}").json()

    steps = await _steps(wf["workflow"])
    assert [ref.split("/")[1] for _, ref, *_ in steps] == ["check", "help", "sign"]
    assert [kind for *_, kind, _n, _i in steps] == ["gate", "task", "approval"]
    # The prompt step kept its private agent; its prompt now comes from the file.
    assert steps[0][2] == wf["vessel"]
    assert body["steps"][0]["agent"]["task_prompt"] == "judge it harder"
    async with SessionLocal() as session:
        helper = (
            await session.execute(
                select(AgentSession).where(AgentSession.export_id == new_agent_id)
            )
        ).scalar_one()
        # A file new to this instance got its own agent row, declared by the file.
        assert steps[1][2] == helper.id and helper.title == "Helper"
        assert not helper.inline
    assert steps[2][2] is None


async def test_a_step_that_comes_back_keeps_its_private_agent(files_mode: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentRun, AgentSession

    wf = await _exported_workflow()
    async with SessionLocal() as session:
        session.add(AgentRun(agent_id=wf["vessel"], trigger="manual", status="completed"))
        await session.commit()
    doc = _load_file(files_mode, wf["path"])
    with TestClient(create_app()) as client:
        client.get(f"/api/workflows/{wf['workflow']}")  # read from its file once
        cut = {**doc, "steps": doc["steps"][:1]}
        _write(files_mode, wf["path"], cut)
        listed = client.get("/api/workflows")
        assert listed.status_code in (200, 500)  # other tests' rows may break the list
        client.get(f"/api/workflows/{wf['workflow']}")
        assert [ref.split("/")[1] for _, ref, *_ in await _steps(wf["workflow"])] == ["write"]
        async with SessionLocal() as session:
            # Kept, detached, with its history: the step may come back.
            assert await session.get(AgentSession, wf["vessel"]) is not None

        _write(files_mode, wf["path"], doc)  # pasted back
        client.get(f"/api/workflows/{wf['workflow']}")
        steps = await _steps(wf["workflow"])
        assert steps[1][2] == wf["vessel"]
        async with SessionLocal() as session:
            runs = (
                (await session.execute(select(AgentRun).where(AgentRun.agent_id == wf["vessel"])))
                .scalars()
                .all()
            )
            assert len(runs) == 1

        _write(files_mode, wf["path"], cut)
        client.get(f"/api/workflows/{wf['workflow']}")
        assert client.delete(f"/api/workflows/{wf['workflow']}").status_code == 204
    async with SessionLocal() as session:
        # With the workflow deleted in the app, its kept agents go too.
        assert await session.get(AgentSession, wf["vessel"]) is None


async def test_a_running_workflow_keeps_its_steps(files_mode: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Workflow

    wf = await _exported_workflow()
    with TestClient(create_app()) as client:
        client.get(f"/api/workflows/{wf['workflow']}")
        before = await _steps(wf["workflow"])
        async with SessionLocal() as session:
            row = await session.get(Workflow, wf["workflow"])
            assert row is not None
            row.status = "running"
            await session.commit()
        doc = yaml.safe_load((files_mode / wf["path"]).read_text(encoding="utf-8"))
        doc["steps"] = doc["steps"][:1]
        _write(files_mode, wf["path"], doc)
        client.get(f"/api/workflows/{wf['workflow']}")
    assert [(p, ref, a) for p, ref, a, *_ in await _steps(wf["workflow"])] == [
        (p, ref, a) for p, ref, a, *_ in before
    ]


async def test_workflow_settings_come_from_the_file(files_mode: Path) -> None:
    wf = await _exported_workflow()
    doc = yaml.safe_load((files_mode / wf["path"]).read_text(encoding="utf-8"))
    doc.update(name="Renamed in file", description="from disk", max_loops=7)
    _write(files_mode, wf["path"], doc)
    with TestClient(create_app()) as client:
        body = client.get(f"/api/workflows/{wf['workflow']}").json()
    assert (body["name"], body["description"], body["max_loops"]) == (
        "Renamed in file",
        "from disk",
        7,
    )
    # Any load sees the file, not just the detail route. (Listing every
    # workflow isn't asserted: the suite's shared database holds rows other
    # tests seeded with values the list schema rejects.)
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Workflow

    async with SessionLocal() as session:
        row = (
            await session.execute(select(Workflow).where(Workflow.id == wf["workflow"]))
        ).scalar_one()
        assert row.name == "Renamed in file"
    name, loops = await _raw("workflows", wf["workflow"], "name", "max_loops")
    assert name.startswith("Flow ") and loops == 3


async def test_a_run_starts_from_the_file_and_records_it(files_mode: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentRun, WorkflowRun
    from precursor.backend.services.agents import workflow as wf_mod

    wf = await _exported_workflow()
    mgr = _FakeManager()
    async with SessionLocal() as session:
        await wf_mod.start_workflow(session, mgr, wf["workflow"])  # type: ignore[arg-type]

    assert mgr.started == [wf["writer"]]
    async with SessionLocal() as session:
        run = (
            (
                await session.execute(
                    select(WorkflowRun)
                    .where(WorkflowRun.workflow_id == wf["workflow"])
                    .order_by(WorkflowRun.id.desc())
                )
            )
            .scalars()
            .first()
        )
        assert run is not None
        assert run.definition_path == wf["path"] and len(run.definition_hash or "") == 64
        agent_run = (
            (
                await session.execute(
                    select(AgentRun)
                    .where(AgentRun.agent_id == wf["writer"])
                    .order_by(AgentRun.id.desc())
                )
            )
            .scalars()
            .first()
        )
        assert agent_run is not None and agent_run.definition_path == wf["writer_path"]


async def test_a_broken_workflow_file_refuses_to_run(files_mode: Path) -> None:
    from precursor.backend.services.definitions import overlay

    wf = await _exported_workflow()
    # Still this workflow's file (same id), but no longer valid: `name` is gone.
    (files_mode / wf["path"]).write_text(f"kind: workflow\nid: {wf['id']}\n", encoding="utf-8")
    overlay.invalidate()
    with TestClient(create_app()) as client:
        resp = client.post(f"/api/workflows/{wf['workflow']}/run")
    assert resp.status_code == 409
    assert "has errors" in resp.json()["detail"]


async def test_export_cannot_overwrite_files_in_files_mode(files_mode: Path) -> None:
    with TestClient(create_app()) as client:
        resp = client.post("/api/definitions/export", params={"overwrite": True})
        assert resp.status_code == 409
        assert client.post("/api/definitions/export").status_code == 200


# --- Lists & detail views (step 5) ------------------------------------------


async def test_a_hand_written_agent_file_joins_the_roster(files_mode: Path) -> None:
    ident = _uid()
    _write(
        files_mode,
        "agents/team/scout.agent.yaml",
        {"kind": "agent", "id": ident, "title": f"Scout {ident}", "prompt": "look around"},
    )
    with TestClient(create_app()) as client:
        listed = [a for a in client.get("/api/agents").json() if a["title"] == f"Scout {ident}"]
        assert len(listed) == 1
        again = [a for a in client.get("/api/agents").json() if a["title"] == f"Scout {ident}"]
    assert len(again) == 1  # adopted once, not on every list
    assert listed[0]["task_prompt"] == "look around"
    definition = listed[0]["definition"]
    assert (definition["state"], definition["path"]) == ("file", "agents/team/scout.agent.yaml")


async def test_moving_a_file_keeps_the_same_agent(files_mode: Path) -> None:
    ident = _uid()
    agent_id = await _agent_row(title="t", task_prompt="p", export_id=ident)
    doc = {"kind": "agent", "id": ident, "title": "Mover"}
    _write(files_mode, "agents/mover.agent.yaml", doc)
    with TestClient(create_app()) as client:
        assert client.get(f"/api/agents/{agent_id}").json()["definition"]["path"] == (
            "agents/mover.agent.yaml"
        )
        (files_mode / "agents/mover.agent.yaml").unlink()
        _write(files_mode, "archive/2026/mover.agent.yaml", doc)
        body = client.get(f"/api/agents/{agent_id}").json()
    # Identity is the id inside the file, so the row — and its history — follow.
    assert body["definition"]["path"] == "archive/2026/mover.agent.yaml"
    assert body["title"] == "Mover"


async def test_the_api_says_when_there_is_no_file_or_a_broken_one(files_mode: Path) -> None:
    orphan = await _agent_row(title="No file", task_prompt="p", export_id=_uid())
    ident = _uid()
    broken = await _agent_row(title="Broken", task_prompt="p", export_id=ident)
    _write(files_mode, "agents/broken.agent.yaml", {"kind": "agent", "id": ident})
    with TestClient(create_app()) as client:
        none = client.get(f"/api/agents/{orphan}").json()["definition"]
        bad = client.get(f"/api/agents/{broken}").json()["definition"]
    assert none["state"] == "none" and none["path"] is None
    assert bad["state"] == "invalid" and bad["path"] == "agents/broken.agent.yaml"
    assert "has errors" in bad["message"]


async def test_database_mode_reports_no_definition(
    files_mode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.config import get_settings

    agent_id = await _agent_row(title="t", task_prompt="p")
    monkeypatch.setattr(get_settings(), "definitions_source", "database")
    with TestClient(create_app()) as client:
        assert client.get(f"/api/agents/{agent_id}").json()["definition"] is None


async def test_a_hand_written_workflow_file_becomes_a_runnable_workflow(files_mode: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Workflow
    from precursor.backend.services.definitions import anchors

    ident, agent_ident = _uid(), _uid()
    _write(
        files_mode,
        "agents/helper.agent.yaml",
        {"kind": "agent", "id": agent_ident, "title": "Helper", "prompt": "help"},
    )
    _write(
        files_mode,
        "workflows/new.workflow.yaml",
        {
            "kind": "workflow",
            "id": ident,
            "name": f"Hand made {ident}",
            "steps": [
                {"key": "help", "agent": "agents/helper.agent.yaml"},
                {"key": "judge", "kind": "gate", "prompt": "PASS if ok", "on_fail": "help"},
            ],
        },
    )
    # What the gallery does on every list.
    async with SessionLocal() as session:
        assert await anchors.adopt_new_files(session)
        await anchors.sync_all(session)
    async with SessionLocal() as session:
        wf = (
            await session.execute(select(Workflow).where(Workflow.export_id == ident))
        ).scalar_one()
        _created_workflows.append(wf.id)
        wf_id = wf.id
    with TestClient(create_app()) as client:
        body = client.get(f"/api/workflows/{wf_id}").json()
    assert body["name"] == f"Hand made {ident}"
    assert body["status"] == "idle"
    assert body["definition"]["path"] == "workflows/new.workflow.yaml"
    assert [(s["kind"], s["on_fail_position"]) for s in body["steps"]] == [
        ("task", None),
        ("gate", 0),
    ]
    assert body["steps"][0]["agent"]["title"] == "Helper"
    assert body["steps"][1]["agent"]["task_prompt"] == "PASS if ok"


# --- In-app edits write the files (step 6) ----------------------------------


def _load_file(root: Path, rel: str) -> dict[str, Any]:
    return yaml.safe_load((root / rel).read_text(encoding="utf-8"))


@pytest.fixture
def runtime_up(monkeypatch: pytest.MonkeyPatch) -> None:
    from precursor.backend.services.agents import runtime

    monkeypatch.setattr(runtime, "agents_available", lambda: (True, "test"))


async def test_editing_a_linked_agent_writes_its_file(files_mode: Path, runtime_up: None) -> None:
    ident = _uid()
    agent_id = await _agent_row(title="t", task_prompt="p", export_id=ident)
    _write(
        files_mode,
        "agents/editor.agent.yaml",
        {"kind": "agent", "id": ident, "title": "Before", "prompt": "old prompt"},
    )
    with TestClient(create_app()) as client:
        resp = client.patch(
            f"/api/agents/{agent_id}",
            json={
                "title": "After",
                "task": "new prompt",
                "approval_policy": "autonomous",
                "use_skills": False,
                "mcp_servers": "fetch",
            },
        )
        assert resp.status_code == 200, resp.text
        assert (resp.json()["title"], resp.json()["task_prompt"]) == ("After", "new prompt")
        again = client.get(f"/api/agents/{agent_id}").json()
    assert again["title"] == "After"
    doc = _load_file(files_mode, "agents/editor.agent.yaml")
    assert doc == {
        "kind": "agent",
        "id": ident,
        "title": "After",
        "prompt": "new prompt",
        "approval_policy": "autonomous",
        "capabilities": {"skills": False, "mcp_servers": ["fetch"]},
    }


async def test_editing_an_agent_without_a_file_creates_it(files_mode: Path) -> None:
    agent_id = await _agent_row(title=f"Fresh {_uid()}", task_prompt="p", chat_id=None)
    with TestClient(create_app()) as client:
        resp = client.patch(f"/api/agents/{agent_id}", json={"max_steps": 7})
        assert resp.status_code == 200, resp.text
        body = client.get(f"/api/agents/{agent_id}").json()
    assert body["definition"]["state"] == "file"
    doc = _load_file(files_mode, body["definition"]["path"])
    assert doc["autonomy"] == {"max_steps": 7}
    assert body["definition"]["path"].startswith("agents/fresh-")


async def test_a_broken_file_is_never_overwritten_from_the_app(files_mode: Path) -> None:
    ident = _uid()
    agent_id = await _agent_row(title="t", task_prompt="p", export_id=ident)
    broken = f"kind: agent\nid: {ident}\ntitel: half-typed\n"
    (files_mode / "agents").mkdir(parents=True, exist_ok=True)
    (files_mode / "agents/wip.agent.yaml").write_text(broken, encoding="utf-8")
    from precursor.backend.services.definitions import overlay

    overlay.invalidate()
    with TestClient(create_app()) as client:
        resp = client.patch(f"/api/agents/{agent_id}", json={"title": "From the app"})
    assert resp.status_code == 409
    assert "has errors" in resp.json()["detail"]
    assert (files_mode / "agents/wip.agent.yaml").read_text(encoding="utf-8") == broken


async def test_workflow_settings_and_steps_saved_in_the_app_reach_the_file(
    files_mode: Path,
) -> None:
    wf = await _exported_workflow()
    with TestClient(create_app()) as client:
        before = client.get(f"/api/workflows/{wf['workflow']}").json()
        resp = client.patch(
            f"/api/workflows/{wf['workflow']}", json={"name": "Renamed in app", "max_loops": 5}
        )
        assert resp.status_code == 200, resp.text
        doc = _load_file(files_mode, wf["path"])
        assert (doc["name"], doc["max_loops"]) == ("Renamed in app", 5)
        assert [s["key"] for s in doc["steps"]] == ["write", "check"]

        # What the step editor sends: the steps swapped, the inline one edited.
        write_step, check_step = before["steps"]
        payload = [
            {
                "agent_id": check_step["agent"]["id"],
                "task": "judge it strictly",
                "kind": "gate",
                "name": "Check",
            },
            {"agent_id": write_step["agent"]["id"], "kind": "task", "name": "Write"},
        ]
        resp = client.patch(f"/api/workflows/{wf['workflow']}", json={"steps": payload})
        assert resp.status_code == 200, resp.text
        after = resp.json()

    doc = _load_file(files_mode, wf["path"])
    # Keys survive the save (the editor recreates every row), so the inline step
    # keeps its private agent and the history that goes with it.
    assert [s["key"] for s in doc["steps"]] == ["check", "write"]
    assert doc["steps"][0]["prompt"] == "judge it strictly"
    assert doc["steps"][1]["agent"] == wf["writer_path"]
    assert after["steps"][0]["agent"]["id"] == wf["vessel"]
    assert after["steps"][0]["agent"]["task_prompt"] == "judge it strictly"
    refs = [ref for _, ref, *_ in await _steps(wf["workflow"])]
    assert refs == [f"{wf['id']}/check", f"{wf['id']}/write"]


async def test_deleting_in_the_app_removes_the_file(files_mode: Path) -> None:
    wf = await _exported_workflow()
    agent_ident = _uid()
    agent_id = await _agent_row(title="Doomed", task_prompt="p", export_id=agent_ident)
    _write(
        files_mode, "agents/doomed.agent.yaml", {"kind": "agent", "id": agent_ident, "title": "D"}
    )
    with TestClient(create_app()) as client:
        assert client.delete(f"/api/workflows/{wf['workflow']}").status_code == 204
        assert client.delete(f"/api/agents/{agent_id}").status_code == 204
        # Not re-adopted as a new row by the next list.
        titles = [a["title"] for a in client.get("/api/agents").json()]
    _created_workflows.remove(wf["workflow"])
    assert not (files_mode / wf["path"]).exists()
    assert not (files_mode / "agents/doomed.agent.yaml").exists()
    assert "D" not in titles


async def test_creating_a_workflow_in_the_app_writes_a_file(files_mode: Path) -> None:
    name = f"Made in app {_uid()}"
    with TestClient(create_app()) as client:
        resp = client.post(
            "/api/workflows",
            json={"name": name, "steps": [{"task": "do one thing", "name": "Only step"}]},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
    _created_workflows.append(body["id"])
    assert body["definition"]["state"] == "file"
    doc = _load_file(files_mode, body["definition"]["path"])
    assert doc["name"] == name
    # (Capabilities are left out: they follow Settings → Workflows defaults,
    # which other tests change in the shared database.)
    [step] = doc["steps"]
    assert (step["key"], step["name"], step["prompt"]) == ("only-step", "Only step", "do one thing")


# --- Trust gate (step 7) ----------------------------------------------------


async def _exported_agent(**columns: Any) -> dict[str, Any]:
    """An agent set up in the app, then exported: its permissions are accepted."""
    from precursor.backend.config import get_settings
    from precursor.backend.db import SessionLocal
    from precursor.backend.services.definitions.exporter import export_definitions

    agent_id = await _agent_row(title=f"Trusted {_uid()}", task_prompt="p", **columns)
    async with SessionLocal() as session:
        result = await export_definitions(session, Path(get_settings().definitions_dir))
    [entry] = [e for e in result.written if e.kind == "agent" and e.source_id == agent_id]
    return {"id": agent_id, "path": entry.path}


async def _review(agent_id: int) -> list[str]:
    with TestClient(create_app()) as client:
        return client.get(f"/api/agents/{agent_id}").json()["definition"]["review"]


async def test_widening_an_agent_on_disk_is_held_for_review(files_mode: Path) -> None:
    agent = await _exported_agent(approval_policy="balanced", mcp_servers="fetch")
    assert await _review(agent["id"]) == []

    doc = _load_file(files_mode, agent["path"])
    doc["approval_policy"] = "manual"  # narrower: fine
    _write(files_mode, agent["path"], doc)
    assert await _review(agent["id"]) == []

    doc.update(
        approval_policy="autonomous",
        autonomy={"enabled": True, "max_steps": 30},
        capabilities={"mcp_servers": ["fetch", "workiq"]},
        limits={"token_budget": 5000},
    )
    _write(files_mode, agent["path"], doc)
    review = await _review(agent["id"])
    assert review == [
        "approval policy → autonomous",
        "runs autonomously",
        "up to 30 autonomous steps",
        "can reach MCP server workiq",
    ]


async def test_a_held_agent_cannot_start(files_mode: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.definitions import trust

    agent = await _exported_agent()
    doc = _load_file(files_mode, agent["path"])
    doc["approval_policy"] = "autonomous"
    _write(files_mode, agent["path"], doc)
    async with SessionLocal() as session:
        row = await session.get(AgentSession, agent["id"])
        assert row is not None
        changes = await trust.pending_changes(session, row)
    assert changes == ["approval policy → autonomous"]
    assert "Accept them in the app" in trust.review_message("x", changes)


async def test_a_file_from_elsewhere_needs_acceptance_once(files_mode: Path) -> None:
    ident = _uid()
    _write(
        files_mode,
        "agents/visitor.agent.yaml",
        {"kind": "agent", "id": ident, "title": f"Visitor {ident}", "approval_policy": "manual"},
    )
    with TestClient(create_app()) as client:
        [row] = [a for a in client.get("/api/agents").json() if a["title"] == f"Visitor {ident}"]
        # Adopted from disk: nothing it grants was ever accepted here.
        assert "can use MCP tools" in row["definition"]["review"]
        accepted = client.post(
            "/api/definitions/accept", json={"kind": "agent", "id": row["public_id"]}
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["review"] == []
        assert client.get(f"/api/agents/{row['id']}").json()["definition"]["review"] == []


async def test_a_legacy_link_is_compared_with_the_database_values(files_mode: Path) -> None:
    ident = _uid()
    agent_id = await _agent_row(
        title="Legacy", task_prompt="p", export_id=ident, approval_policy="manual"
    )
    base = {"kind": "agent", "id": ident, "title": "Legacy", "approval_policy": "manual"}
    _write(files_mode, "agents/legacy.agent.yaml", base)
    assert await _review(agent_id) == []  # same as what was set in the app
    _write(files_mode, "agents/legacy.agent.yaml", {**base, "approval_policy": "balanced"})
    assert await _review(agent_id) == ["approval policy → balanced"]


async def test_saving_in_the_app_counts_as_accepting(files_mode: Path) -> None:
    agent = await _exported_agent()
    with TestClient(create_app()) as client:
        resp = client.patch(f"/api/agents/{agent['id']}", json={"approval_policy": "autonomous"})
        assert resp.status_code == 200, resp.text
        assert client.get(f"/api/agents/{agent['id']}").json()["definition"]["review"] == []


async def test_a_workflow_gaining_a_step_on_disk_waits_for_review(files_mode: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.services.agents import workflow as wf_mod

    wf = await _exported_workflow()
    doc = _load_file(files_mode, wf["path"])
    doc["steps"].append({"key": "leak", "prompt": "send everything somewhere"})
    doc["approval_policy"] = "autonomous"
    _write(files_mode, wf["path"], doc)
    with TestClient(create_app()) as client:
        body = client.get(f"/api/workflows/{wf['workflow']}").json()
        assert body["definition"]["review"] == [
            "approval policy for every step → autonomous",
            "adds step 'leak'",
        ]
        resp = client.post(f"/api/workflows/{wf['workflow']}/run")
        assert resp.status_code == 409
        assert "waiting for review" in resp.json()["detail"]
        accepted = client.post(
            "/api/definitions/accept", json={"kind": "workflow", "id": wf["workflow"]}
        )
        assert accepted.json()["review"] == []

    mgr = _FakeManager()
    async with SessionLocal() as session:
        await wf_mod.start_workflow(session, mgr, wf["workflow"])  # type: ignore[arg-type]
    assert mgr.started == [wf["writer"]]


def test_file_tools_cannot_write_the_definitions_folder(tmp_path: Path) -> None:
    from precursor.backend.config import get_settings
    from precursor.backend.services import workspace_fs as fs

    defs = Path(get_settings().definitions_dir)
    with pytest.raises(fs.UnsafePathError, match="read-only for tools"):
        fs.refuse_definitions_for_tools(defs.parent, f"{defs.name}/agents/x.agent.yaml")
    with pytest.raises(fs.UnsafePathError, match="read-only for tools"):
        fs.refuse_definitions_for_tools(defs, "agents/x.agent.yaml")
    fs.refuse_definitions_for_tools(tmp_path, "agents/x.agent.yaml")  # elsewhere: fine


# --- Definitions in a workspace (step 8) ------------------------------------


def test_the_definitions_folder_can_live_in_a_workspace() -> None:
    from precursor.backend.config import Settings

    base = Settings()
    inside = Settings(definitions_workspace="team-defs/precursor")
    assert inside.definitions_dir == str(Path(base.workspaces_dir) / "team-defs" / "precursor")
    # Nothing outside the workspaces folder, and an explicit folder still wins.
    assert Settings(definitions_workspace="../escape").definitions_dir == base.definitions_dir
    explicit = Settings(definitions_workspace="team-defs", PRECURSOR_DEFINITIONS_DIR="/tmp/x")
    assert explicit.definitions_dir == str(Path("/tmp/x").resolve())


async def _workspace_holding(files_mode: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, Path]:
    """A folder workspace whose ``defs/`` sub-folder is the definitions folder."""
    from precursor.backend.config import get_settings
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Workspace
    from precursor.backend.services.definitions import overlay

    slug = f"defs-{_uid()}"
    async with SessionLocal() as session:
        ws = Workspace(name="Definitions", slug=slug, kind="folder")
        session.add(ws)
        await session.commit()
        ws_id = ws.id
    root = Path(get_settings().workspaces_dir) / slug
    (root / "defs").mkdir(parents=True)
    monkeypatch.setattr(get_settings(), "definitions_dir", str(root / "defs"))
    overlay.invalidate()
    return ws_id, root


async def test_files_shows_the_check_for_a_definition_file(
    files_mode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws_id, root = await _workspace_holding(files_mode, monkeypatch)
    _write(root / "defs", "agents/ok.agent.yaml", {"kind": "agent", "id": _uid(), "title": "Ok"})
    _write(
        root / "defs",
        "workflows/w.workflow.yaml",
        {
            "kind": "workflow",
            "id": _uid(),
            "name": "W",
            "steps": [{"key": "a", "agent": "agents/missing.agent.yaml"}],
        },
    )
    (root / "notes.md").write_text("# not a definition", encoding="utf-8")
    with TestClient(create_app()) as client:

        def report(path: str) -> dict[str, Any]:
            resp = client.get(
                "/api/definitions/file-issues", params={"workspace_id": ws_id, "path": path}
            )
            assert resp.status_code == 200, resp.text
            return resp.json()

        ok = report("defs/agents/ok.agent.yaml")
        broken = report("defs/workflows/w.workflow.yaml")
        outside = report("notes.md")
    assert (ok["in_definitions"], ok["valid"], ok["issues"]) == (True, True, [])
    assert broken["path"] == "workflows/w.workflow.yaml" and broken["valid"] is True
    assert [i["location"] for i in broken["issues"]] == ["steps[a].agent"]
    assert outside == {
        "in_definitions": False,
        "path": None,
        "kind": None,
        "valid": None,
        "issues": [],
    }


async def test_saving_a_definition_in_files_applies_at_once(
    files_mode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws_id, root = await _workspace_holding(files_mode, monkeypatch)
    ident = _uid()
    agent_id = await _agent_row(title="t", task_prompt="p", export_id=ident)
    _write(root / "defs", "agents/a.agent.yaml", {"kind": "agent", "id": ident, "title": "One"})
    with TestClient(create_app()) as client:
        assert client.get(f"/api/agents/{agent_id}").json()["title"] == "One"
        resp = client.put(
            f"/api/workspaces/{ws_id}/file",
            params={"path": "defs/agents/a.agent.yaml"},
            json={"content": f"kind: agent\nid: {ident}\ntitle: Two\n"},
        )
        assert resp.status_code == 200, resp.text
        # No wait for the folder cache to expire.
        assert client.get(f"/api/agents/{agent_id}").json()["title"] == "Two"


async def test_a_pull_applies_new_files_at_once(
    files_mode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.routers.workspaces import _holds_definitions
    from precursor.backend.services.definitions import anchors

    _ws_id, root = await _workspace_holding(files_mode, monkeypatch)
    assert _holds_definitions(root)
    assert not _holds_definitions(root.parent / "elsewhere")
    ident = _uid()
    _write(root / "defs", "agents/pulled.agent.yaml", {"kind": "agent", "id": ident, "title": "P"})
    async with SessionLocal() as session:
        await anchors.refresh(session)  # what git_pull runs after a successful pull
    async with SessionLocal() as session:
        pulled = (
            await session.execute(select(AgentSession).where(AgentSession.export_id == ident))
        ).scalar_one()
    assert pulled.title == "P"


async def test_the_assistant_cannot_write_definitions_in_a_workspace(
    files_mode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.services.mcp import workspace_fs_server as srv

    ws_id, root = await _workspace_holding(files_mode, monkeypatch)
    written = await srv.write_file(
        workspace_id=ws_id, path="defs/agents/evil.agent.yaml", content="kind: agent\n"
    )
    assert "read-only for tools" in written["error"]
    assert not (root / "defs/agents/evil.agent.yaml").exists()
    ok = await srv.write_file(workspace_id=ws_id, path="notes/plan.md", content="# fine")
    assert "error" not in ok


async def test_the_runtime_knows_the_step_keys_of_a_file_workflow(files_mode: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.services.workflow_state import _step_keys

    wf = await _exported_workflow()
    with TestClient(create_app()) as client:
        client.get(f"/api/workflows/{wf['workflow']}")  # adopt the rows
    async with SessionLocal() as session:
        assert await _step_keys(session, wf["workflow"]) == {"write": 0, "check": 1}


async def test_two_lists_racing_to_adopt_a_file_do_not_fail(files_mode: Path) -> None:
    import asyncio

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.definitions import anchors

    ident = _uid()
    _write(files_mode, "agents/racy.agent.yaml", {"kind": "agent", "id": ident, "title": "Racy"})

    async def adopt() -> bool:
        async with SessionLocal() as session:
            return await anchors.adopt_new_files(session)

    results = await asyncio.gather(adopt(), adopt())
    assert True in results  # one of them adopted it; neither raised
    async with SessionLocal() as session:
        rows = (
            (await session.execute(select(AgentSession).where(AgentSession.export_id == ident)))
            .scalars()
            .all()
        )
    assert len(rows) == 1


# --- Review findings --------------------------------------------------------


async def _set_status(workflow_id: int, status: str) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Workflow

    async with SessionLocal() as session:
        row = await session.get(Workflow, workflow_id)
        assert row is not None
        row.status = status
        await session.commit()


async def test_a_running_workflow_keeps_the_file_version_it_started_from(
    files_mode: Path,
) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.services.agents import workflow as wf_mod
    from precursor.backend.services.definitions import overlay

    wf = await _exported_workflow()
    mgr = _FakeManager()
    async with SessionLocal() as session:
        await wf_mod.start_workflow(session, mgr, wf["workflow"])  # type: ignore[arg-type]
    await _set_status(wf["workflow"], "awaiting_approval")

    doc = _load_file(files_mode, wf["path"])
    doc["approval_policy"] = "autonomous"
    doc["steps"][1]["capabilities"] = {"mcp": True}
    _write(files_mode, wf["path"], doc)
    async with SessionLocal() as session:
        running = await wf_mod._load_workflow(session, wf["workflow"])
        assert running is not None
        # Neither the widened policy nor the step's new tools reach the run.
        assert running.approval_policy is None
        assert running.steps[1].use_mcp is None

    # After a restart the pin is gone: continuing needs the review first.
    overlay._pins.clear()
    with TestClient(create_app()) as client:
        resp = client.post(f"/api/workflows/{wf['workflow']}/approve", json={})
    assert resp.status_code == 409
    assert "waiting for review" in resp.json()["detail"]


async def test_a_run_cannot_continue_on_reshaped_steps_after_a_restart(files_mode: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.services.agents import workflow as wf_mod
    from precursor.backend.services.definitions import overlay

    wf = await _exported_workflow()
    async with SessionLocal() as session:
        await wf_mod.start_workflow(session, _FakeManager(), wf["workflow"])  # type: ignore[arg-type]
    await _set_status(wf["workflow"], "paused")
    doc = _load_file(files_mode, wf["path"])
    doc["steps"].reverse()  # a reorder, no permission change
    _write(files_mode, wf["path"], doc)
    overlay._pins.clear()
    with TestClient(create_app()) as client:
        resp = client.post(f"/api/workflows/{wf['workflow']}/resume", json={})
    assert resp.status_code == 409
    assert "changed since this run started" in resp.json()["detail"]


async def test_a_run_uses_its_own_approval_policy_not_the_file_s_live_one(
    files_mode: Path,
) -> None:
    from precursor.backend.models import AgentRun, AgentSession
    from precursor.backend.services.agents import permissions

    agent = AgentSession(title="t", task_prompt="p", approval_policy="autonomous")
    run = AgentRun(agent_id=1, trigger="manual", status="running", approval_policy=None)
    live = await permissions.approval_policy(agent, run)
    assert live != "autonomous"  # the global default, frozen for the run


async def test_saving_in_the_app_is_refused_while_a_review_is_pending(files_mode: Path) -> None:
    agent = await _exported_agent()
    doc = _load_file(files_mode, agent["path"])
    doc["autonomy"] = {"enabled": True, "max_steps": 50}
    _write(files_mode, agent["path"], doc)
    with TestClient(create_app()) as client:
        resp = client.patch(f"/api/agents/{agent['id']}", json={"title": "Renamed"})
        assert resp.status_code == 409
        assert "waiting for review" in resp.json()["detail"]
        assert client.get(f"/api/agents/{agent['id']}").json()["definition"]["review"]


async def test_a_settings_save_keeps_steps_edited_in_the_file(files_mode: Path) -> None:
    wf = await _exported_workflow()
    with TestClient(create_app()) as client:
        client.get(f"/api/workflows/{wf['workflow']}")
        await _set_status(wf["workflow"], "paused")  # rows won't follow the file now
        doc = _load_file(files_mode, wf["path"])
        doc["steps"].append({"key": "extra", "kind": "approval"})
        _write(files_mode, wf["path"], doc)
        client.post("/api/definitions/accept", json={"kind": "workflow", "id": wf["workflow"]})
        resp = client.patch(f"/api/workflows/{wf['workflow']}", json={"name": "Renamed flow"})
        assert resp.status_code == 200, resp.text
    saved = _load_file(files_mode, wf["path"])
    assert saved["name"] == "Renamed flow"
    assert [s["key"] for s in saved["steps"]] == ["write", "check", "extra"]


async def test_a_step_whose_agent_file_is_gone_blocks_the_run(files_mode: Path) -> None:
    wf = await _exported_workflow()
    (files_mode / wf["writer_path"]).rename(files_mode / "moved.agent.yaml")
    from precursor.backend.services.definitions import overlay

    overlay.invalidate()
    with TestClient(create_app()) as client:
        resp = client.post(f"/api/workflows/{wf['workflow']}/run")
    assert resp.status_code == 409
    assert wf["writer_path"] in resp.json()["detail"]


def test_removing_or_bypassing_an_approval_needs_review() -> None:
    from precursor.backend.services.definitions.trust import _workflow_changes

    def snap(*steps: tuple[str, str]) -> dict[str, Any]:
        return {
            "approval_policy": None,
            "order": [k for k, _ in steps],
            "steps": {
                k: {
                    "kind": kind,
                    "agent": None,
                    "prompt": kind != "approval",
                    "mcp": None,
                    "skills": None,
                    "memory": None,
                    "mcp_servers": None,
                }
                for k, kind in steps
            },
        }

    before = snap(("draft", "task"), ("ok", "approval"), ("publish", "task"))
    assert _workflow_changes(before, snap(("draft", "task"), ("publish", "task"))) == [
        "removes approval step 'ok'"
    ]
    moved = snap(("draft", "task"), ("publish", "task"), ("ok", "approval"))
    assert _workflow_changes(before, moved) == ["step 'publish' no longer waits for approval 'ok'"]
    assert _workflow_changes(before, before) == []


async def test_a_copied_file_with_the_same_id_refuses_to_run(files_mode: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.definitions import overlay

    agent = await _exported_agent()
    shutil.copy(files_mode / agent["path"], files_mode / "agents/copy.agent.yaml")
    overlay.invalidate()
    async with SessionLocal() as session:
        row = await session.get(AgentSession, agent["id"])
        assert row is not None
        problem = overlay.definition_error(row)
        source = overlay.source_of(row)
    assert problem is not None and "used by several files" in problem
    assert source is not None and source.state == "invalid"


async def test_accept_refuses_a_file_that_changed_since_it_was_reviewed(files_mode: Path) -> None:
    agent = await _exported_agent()
    doc = _load_file(files_mode, agent["path"])
    doc["approval_policy"] = "autonomous"
    _write(files_mode, agent["path"], doc)
    with TestClient(create_app()) as client:
        reviewed = client.get(f"/api/agents/{agent['id']}").json()["definition"]
        doc["capabilities"] = {"mcp_servers": ["everything"]}
        _write(files_mode, agent["path"], doc)  # lands after the review was shown
        stale = client.post(
            "/api/definitions/accept",
            json={"kind": "agent", "id": agent["id"], "content_hash": reviewed["content_hash"]},
        )
        assert stale.status_code == 409
        fresh = client.get(f"/api/agents/{agent['id']}").json()["definition"]
        ok = client.post(
            "/api/definitions/accept",
            json={"kind": "agent", "id": agent["id"], "content_hash": fresh["content_hash"]},
        )
        assert ok.status_code == 200 and ok.json()["review"] == []


async def test_a_workflow_linked_before_the_review_existed_is_reviewed_once(
    files_mode: Path,
) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Workflow
    from precursor.backend.services.definitions import trust

    wf = await _exported_workflow()
    async with SessionLocal() as session:
        row = await session.get(Workflow, wf["workflow"])
        assert row is not None
        row.accepted_permissions = None  # as if linked some other way
        await session.commit()
    async with SessionLocal() as session:
        row = await session.get(Workflow, wf["workflow"])
        assert row is not None
        assert await trust.pending_changes(session, row) == [
            "adds step 'write'",
            "adds step 'check'",
        ]
