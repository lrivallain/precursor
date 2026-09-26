"""The built-in definitions workspace, and migrating to (and back from) files mode.

The migration is what lets the database's declaration columns go one day: it
must write a file for everything, verify each against the database, and switch
without changing how anything runs — and be reversible until then.
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
from tests.definitions_support import mark_database, restore_database, wipe_agents_and_workflows


def _uid() -> str:
    return "t" + uuid.uuid4().hex[:8]


@pytest.fixture
async def env(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Path]:
    from precursor.backend.config import get_settings
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AppSetting
    from precursor.backend.services.definitions import overlay

    mark = await mark_database()
    await wipe_agents_and_workflows()
    async with SessionLocal() as session:
        for key, value in (("agents_enabled", True), ("definitions_source", "database")):
            row = await session.get(AppSetting, key)
            if row is None:
                session.add(AppSetting(key=key, value=json.dumps(value)))
            else:
                row.value = json.dumps(value)
        await session.commit()
        await overlay.refresh_source(session)
        await overlay.refresh_role_cache(session)
    settings = get_settings()
    monkeypatch.setattr(settings, "definitions_source", "database")
    root = Path(settings.definitions_dir)
    shutil.rmtree(root, ignore_errors=True)
    overlay.invalidate()
    yield root
    overlay.invalidate()
    overlay._pins.clear()
    shutil.rmtree(root, ignore_errors=True)
    await restore_database(mark)


def _write(root: Path, rel: str, content: dict[str, Any] | str) -> None:
    from precursor.backend.services.definitions import overlay

    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    body = content if isinstance(content, str) else yaml.safe_dump(content, sort_keys=False)
    path.write_text(body, encoding="utf-8")
    overlay.invalidate()


def _load(root: Path, rel: str) -> dict[str, Any]:
    return yaml.safe_load((root / rel).read_text(encoding="utf-8"))


async def _raw(table: str, row_id: int, *cols: str) -> tuple[Any, ...]:
    from precursor.backend.db import SessionLocal

    async with SessionLocal() as session:
        result = await session.execute(
            text(f"select {', '.join(cols)} from {table} where id = :id"), {"id": row_id}
        )
        return tuple(result.one())


async def _seed() -> dict[str, Any]:
    """Agents and a workflow built in the app (database mode)."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession, Workflow, WorkflowStep

    tag = _uid()
    async with SessionLocal() as session:
        writer = AgentSession(
            title=f"Writer {tag}",
            task_prompt="write it",
            status="waiting",
            approval_policy="manual",
        )
        reviewer = AgentSession(title=f"Reviewer {tag}", task_prompt="review it", status="waiting")
        vessel = AgentSession(
            title="Gate", task_prompt="PASS if fine", status="waiting", inline=True
        )
        session.add_all([writer, reviewer, vessel])
        await session.flush()
        wf = Workflow(name=f"Pipeline {tag}", status="idle", max_loops=4)
        session.add(wf)
        await session.flush()
        session.add_all(
            [
                WorkflowStep(workflow_id=wf.id, position=0, agent_id=writer.id, name="Write"),
                WorkflowStep(
                    workflow_id=wf.id,
                    position=1,
                    agent_id=vessel.id,
                    kind="gate",
                    name="Gate",
                    instructions="Check {{step.0.output}}",
                ),
            ]
        )
        await session.commit()
        return {
            "writer": writer.id,
            "reviewer": reviewer.id,
            "vessel": vessel.id,
            "workflow": wf.id,
            "tag": tag,
        }


def _items(preview: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(i["kind"], i["name"]): i for i in preview["items"]}


# --- The built-in workspace -------------------------------------------------


async def test_the_builtin_workspace_hosts_the_definitions(env: Path) -> None:
    with TestClient(create_app()) as client:
        listed = client.get("/api/workspaces").json()
    first = listed[0]
    assert (first["slug"], first["name"], first["kind"]) == (
        "definitions",
        "Agents & workflows",
        "local",
    )
    assert first["hosts_definitions"] is True
    assert all(not w["hosts_definitions"] for w in listed[1:])
    assert env.is_dir()


async def test_a_user_workspace_cannot_take_the_reserved_slug(env: Path) -> None:
    with TestClient(create_app()) as client:
        resp = client.post("/api/workspaces", json={"name": "definitions", "kind": "local"})
        assert resp.status_code == 201, resp.text
        assert resp.json()["slug"] == "definitions-2"
        assert client.delete(f"/api/workspaces/{resp.json()['id']}").status_code == 204


async def test_the_definitions_workspace_is_not_deleted_with_its_files(env: Path) -> None:
    with TestClient(create_app()) as client:
        builtin = next(w for w in client.get("/api/workspaces").json() if w["hosts_definitions"])
        _write(env, "agents/keep.agent.yaml", {"kind": "agent", "id": _uid(), "title": "Keep"})
        resp = client.delete(f"/api/workspaces/{builtin['id']}")
    assert resp.status_code == 409
    assert "definition files" in resp.json()["detail"]
    assert (env / "agents/keep.agent.yaml").exists()


# --- Preview ----------------------------------------------------------------


async def test_the_preview_says_what_happens_to_each_file(env: Path) -> None:
    ids = await _seed()
    with TestClient(create_app()) as client:
        # An earlier export: every row linked, every file matching.
        client.post("/api/definitions/export")
        clean = client.get("/api/definitions/migration").json()
        assert clean["source"] == "database" and clean["ready"] is True
        assert {i["action"] for i in clean["items"]} == {"unchanged"}
        assert clean["needs_confirmation"] is False
        assert clean["workspace"]["slug"] == "definitions"

        writer = next(
            i for i in clean["items"] if i["id"] == ids["writer"] and i["kind"] == "agent"
        )
        reviewer = next(
            i for i in clean["items"] if i["id"] == ids["reviewer"] and i["kind"] == "agent"
        )
        doc = _load(env, writer["path"])
        _write(env, writer["path"], {**doc, "title": "Edited on disk"})
        reviewer_id = _load(env, reviewer["path"])["id"]
        _write(env, reviewer["path"], {"kind": "agent", "id": reviewer_id, "titel": "typo"})
        _write(env, "agents/garbled.agent.yaml", "kind: agent\nid: [broken\n")
        _write(
            env, "agents/visitor.agent.yaml", {"kind": "agent", "id": _uid(), "title": "Visitor"}
        )
        # A row created after the export.
        from precursor.backend.db import SessionLocal
        from precursor.backend.models import AgentSession

        async with SessionLocal() as session:
            session.add(AgentSession(title=f"Late {ids['tag']}", task_prompt="p", status="waiting"))
            await session.commit()

        preview = client.get("/api/definitions/migration").json()
    items = _items(preview)
    assert items[("agent", f"Writer {ids['tag']}")]["action"] == "regenerate"
    assert "title" in items[("agent", f"Writer {ids['tag']}")]["reason"]
    assert items[("agent", f"Reviewer {ids['tag']}")]["reason"] == "its file has errors"
    assert items[("agent", f"Late {ids['tag']}")]["action"] == "create"
    assert items[("agent", "Visitor")]["action"] == "new_from_disk"
    assert items[("workflow", f"Pipeline {ids['tag']}")]["action"] == "unchanged"
    assert preview["needs_confirmation"] is True and preview["ready"] is True
    garbled = [i for i in preview["issues"] if i["path"] == "agents/garbled.agent.yaml"]
    assert garbled and "left as it is" in garbled[0]["message"]


async def test_the_preview_lists_blockers(env: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Workflow

    ids = await _seed()
    async with SessionLocal() as session:
        wf = await session.get(Workflow, ids["workflow"])
        assert wf is not None
        wf.status = "running"
        await session.commit()
    ident = _uid()
    _write(env, "agents/a.agent.yaml", {"kind": "agent", "id": ident, "title": "A"})
    _write(env, "agents/b.agent.yaml", {"kind": "agent", "id": ident, "title": "B"})
    with TestClient(create_app()) as client:
        preview = client.get("/api/definitions/migration").json()
        refused = client.post("/api/definitions/migrate", json={"acknowledge": True})
    assert preview["ready"] is False
    joined = " ".join(preview["blockers"])
    assert "mid-run" in joined and "used by several files" in joined
    assert refused.status_code == 409


# --- Migrate ----------------------------------------------------------------


async def test_rewriting_files_needs_confirmation(env: Path) -> None:
    await _seed()
    with TestClient(create_app()) as client:
        client.post("/api/definitions/export")
        item = next(
            i
            for i in client.get("/api/definitions/migration").json()["items"]
            if i["kind"] == "agent"
        )
        doc = _load(env, item["path"])
        _write(env, item["path"], {**doc, "prompt": "changed by hand"})
        resp = client.post("/api/definitions/migrate", json={})
    assert resp.status_code == 409
    assert "confirm" in resp.json()["detail"]


async def test_migrating_switches_to_files_without_changing_anything(env: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession, AppSetting, WorkflowStep
    from precursor.backend.services.definitions import overlay, trust

    ids = await _seed()
    with TestClient(create_app()) as client:
        client.post("/api/definitions/export")
        preview = client.get("/api/definitions/migration").json()
        writer = next(
            i for i in preview["items"] if i["id"] == ids["writer"] and i["kind"] == "agent"
        )
        reviewer = next(
            i for i in preview["items"] if i["id"] == ids["reviewer"] and i["kind"] == "agent"
        )
        # A hand comment in a file that matches: must survive the migration.
        path = env / reviewer["path"]
        path.write_text(path.read_text(encoding="utf-8") + "# my note\n", encoding="utf-8")
        # A file that drifted: the database wins before the switch.
        _write(env, writer["path"], {**_load(env, writer["path"]), "title": "Drifted"})
        _write(
            env, "agents/visitor.agent.yaml", {"kind": "agent", "id": _uid(), "title": "Visitor"}
        )

        resp = client.post("/api/definitions/migrate", json={"acknowledge": True})
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert result["ok"] is True and result["source"] == "files"
        assert (result["regenerated"], result["unchanged"], result["added_from_disk"]) == (1, 2, 1)
        assert result["snapshot"] and Path(result["snapshot"]).is_file()

        assert overlay.files_mode()
        body = client.get(f"/api/agents/{ids['writer']}").json()
        assert body["title"] == f"Writer {ids['tag']}"
        assert body["definition"]["state"] == "file" and body["definition"]["review"] == []
        agents = client.get("/api/agents").json()
    assert path.read_text(encoding="utf-8").endswith("# my note\n")
    assert _load(env, writer["path"])["title"] == f"Writer {ids['tag']}"

    async with SessionLocal() as session:
        setting = await session.get(AppSetting, "definitions_source")
        assert setting is not None and json.loads(setting.value) == "files"
        refs = (
            (
                await session.execute(
                    select(WorkflowStep.definition_ref)
                    .where(WorkflowStep.workflow_id == ids["workflow"])
                    .order_by(WorkflowStep.position)
                )
            )
            .scalars()
            .all()
        )
        assert all(refs) and len(refs) == 2
        writer_row = await session.get(AgentSession, ids["writer"])
        assert writer_row is not None and await trust.pending_changes(session, writer_row) == []
    visitor = next(a for a in agents if a["title"] == "Visitor")
    assert visitor["definition"]["review"]  # arrived from disk: reviewed once


async def test_the_migration_is_remembered_across_restarts(env: Path) -> None:
    from precursor.backend.services.definitions import overlay

    await _seed()
    with TestClient(create_app()) as client:
        assert client.post("/api/definitions/migrate", json={"acknowledge": True}).json()["ok"]
    overlay._persisted_source = None  # a new process
    with TestClient(create_app()) as client:
        assert overlay.files_mode()
        preview = client.get("/api/definitions/migration").json()
    assert preview["source"] == "files"
    assert "already declared by their files" in preview["blockers"][0]
    assert preview["sample_path"].endswith(".workflow.yaml")  # "Open in Files"


# --- Revert -----------------------------------------------------------------


async def test_reverting_copies_the_files_back_into_the_database(env: Path) -> None:
    from precursor.backend.services.definitions import overlay

    ids = await _seed()
    with TestClient(create_app()) as client:
        client.post("/api/definitions/migrate", json={"acknowledge": True})
        writer = client.get(f"/api/agents/{ids['writer']}").json()["definition"]["path"]
        _write(env, writer, {**_load(env, writer), "prompt": "edited as a file"})
        wf = client.get(f"/api/workflows/{ids['workflow']}").json()
        wf_path = wf["definition"]["path"]
        wf_doc = _load(env, wf_path)
        wf_doc["name"] = "Renamed as a file"
        wf_doc["steps"][1]["prompt"] = "PASS only if perfect"
        _write(env, wf_path, wf_doc)

        resp = client.post("/api/definitions/revert")
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert (result["ok"], result["source"], result["skipped"]) == (True, "database", [])
        assert not overlay.files_mode()
        assert (
            client.get(f"/api/agents/{ids['writer']}").json()["task_prompt"] == "edited as a file"
        )
    assert await _raw("agent_sessions", ids["writer"], "task_prompt") == ("edited as a file",)
    assert await _raw("workflows", ids["workflow"], "name") == ("Renamed as a file",)
    assert await _raw("agent_sessions", ids["vessel"], "task_prompt") == ("PASS only if perfect",)


async def test_revert_is_refused_when_files_mode_is_forced(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.config import get_settings

    monkeypatch.setattr(get_settings(), "definitions_source", "files")
    with TestClient(create_app()) as client:
        resp = client.post("/api/definitions/revert")
    assert resp.status_code == 409
    assert "PRECURSOR_DEFINITIONS_SOURCE" in resp.json()["detail"]
