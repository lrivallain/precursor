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
from sqlalchemy import delete, select, text

from precursor.backend.main import create_app

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
    from precursor.backend.db import SessionLocal, init_db
    from precursor.backend.models import AppSetting, Workflow, WorkflowStep
    from precursor.backend.services.definitions import overlay

    await init_db()
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
    shutil.rmtree(root, ignore_errors=True)
    if _created_workflows:
        async with SessionLocal() as session:
            await session.execute(
                delete(WorkflowStep).where(WorkflowStep.workflow_id.in_(_created_workflows))
            )
            await session.execute(delete(Workflow).where(Workflow.id.in_(_created_workflows)))
            await session.commit()
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


async def test_removing_a_prompt_step_deletes_its_private_agent(files_mode: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    wf = await _exported_workflow()
    doc = yaml.safe_load((files_mode / wf["path"]).read_text(encoding="utf-8"))
    doc["steps"] = doc["steps"][:1]
    _write(files_mode, wf["path"], doc)
    with TestClient(create_app()) as client:
        client.get(f"/api/workflows/{wf['workflow']}")
    assert [ref.split("/")[1] for _, ref, *_ in await _steps(wf["workflow"])] == ["write"]
    async with SessionLocal() as session:
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
    assert listed[0]["definition"] == {
        "state": "file",
        "path": "agents/team/scout.agent.yaml",
        "message": None,
    }


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
