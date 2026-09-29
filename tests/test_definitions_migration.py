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
from datetime import UTC, datetime
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
        "Definitions",
        "local",
    )
    assert first["hosts_definitions"] is True
    assert all(not w["hosts_definitions"] for w in listed[1:])
    assert env.is_dir()


async def test_the_builtin_workspace_takes_its_new_name(env: Path) -> None:
    from sqlalchemy import update

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Workspace
    from precursor.backend.services.definitions.home import ensure_definitions_workspace

    async with SessionLocal() as session:
        await ensure_definitions_workspace(session)
        await session.execute(
            update(Workspace)
            .where(Workspace.slug == "definitions")
            .values(name="Agents & workflows")
        )
        await session.commit()
        ws = await ensure_definitions_workspace(session)
        assert ws is not None and ws.name == "Definitions"


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


async def test_summary_templates_are_left_out_of_the_migration(env: Path) -> None:
    await _seed()
    _write(env, "summaries/ok.summary.yaml", "kind: summary\nid: ok\nname: Ok\nprompt: Ok.\n")
    _write(env, "summaries/broken.summary.yaml", "kind: summary\nid: broken\nname: Broken\n")
    _write(env, "summaries/a.summary.yaml", "kind: summary\nid: twin\nname: A\nprompt: a\n")
    _write(env, "summaries/b.summary.yaml", "kind: summary\nid: twin\nname: B\nprompt: b\n")
    with TestClient(create_app()) as client:
        client.post("/api/definitions/export")
        preview = client.get("/api/definitions/migration").json()
        assert preview["ready"] is True, preview["blockers"]
        assert not [i for i in preview["items"] if (i["path"] or "").startswith("summaries/")]
        assert client.post("/api/definitions/migrate", json={"acknowledge": True}).json()["ok"]
        cleanup = client.get("/api/definitions/migration").json()["cleanup"]
    assert not [b for b in cleanup["blockers"] if "summaries/" in b]


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


# --- Wizard: stages, content, cleanup ---------------------------------------


async def test_the_preview_follows_the_stages(env: Path) -> None:
    await _seed()
    with TestClient(create_app()) as client:
        first = client.get("/api/definitions/migration").json()
        assert (first["stage"], first["migrated"], first["finalized"]) == ("database", None, None)
        client.post("/api/definitions/migrate", json={"acknowledge": True})
        second = client.get("/api/definitions/migration").json()
        assert second["stage"] == "files"
        assert second["migrated"]["created"] == 3 and second["migrated"]["snapshot"]
        assert second["cleanup"]["ready"] is True
        assert (second["cleanup"]["agents"], second["cleanup"]["workflows"]) == (2, 1)
        assert (second["cleanup"]["step_prompts"], second["cleanup"]["steps"]) == (1, 2)
        client.post("/api/definitions/finalize", json={"confirm": True})
        third = client.get("/api/definitions/migration").json()
    assert third["stage"] == "finalized"
    assert third["finalized"]["agents"] == 2 and third["finalized"]["snapshot"]


async def test_the_review_shows_the_actual_files(env: Path) -> None:
    ids = await _seed()
    with TestClient(create_app()) as client:
        new = client.get(f"/api/definitions/migration/items/workflow/{ids['workflow']}").json()
        assert new["action"] == "create" and new["current"] is None
        assert "Write" in new["proposed"] and "{{step.write.output}}" in new["proposed"]

        client.post("/api/definitions/export")
        preview = client.get("/api/definitions/migration").json()
        writer = next(
            i for i in preview["items"] if i["id"] == ids["writer"] and i["kind"] == "agent"
        )
        _write(env, writer["path"], {**_load(env, writer["path"]), "prompt": "hand edit"})
        detail = client.get(f"/api/definitions/migration/items/agent/{ids['writer']}").json()
        missing = client.get("/api/definitions/migration/items/agent/999999")
    assert detail["action"] == "regenerate"
    assert "prompt: hand edit" in detail["current"]
    assert "prompt: write it" in detail["proposed"]
    assert missing.status_code == 404


async def test_the_cleanup_needs_confirmation_and_files_mode(env: Path) -> None:
    await _seed()
    with TestClient(create_app()) as client:
        not_yet = client.post("/api/definitions/finalize", json={"confirm": True})
        assert not_yet.status_code == 409 and "Migrate" in not_yet.json()["detail"]
        client.post("/api/definitions/migrate", json={"acknowledge": True})
        unconfirmed = client.post("/api/definitions/finalize", json={})
    assert unconfirmed.status_code == 409 and "can't be undone" in unconfirmed.json()["detail"]


async def test_the_cleanup_is_blocked_by_a_broken_file(env: Path) -> None:
    ids = await _seed()
    with TestClient(create_app()) as client:
        client.post("/api/definitions/migrate", json={"acknowledge": True})
        path = client.get(f"/api/agents/{ids['writer']}").json()["definition"]["path"]
        _write(env, path, {**_load(env, path), "titel": "typo"})
        preview = client.get("/api/definitions/migration").json()
        resp = client.post("/api/definitions/finalize", json={"confirm": True})
    assert preview["cleanup"]["ready"] is False
    assert any(path in b for b in preview["cleanup"]["blockers"])
    assert resp.status_code == 409


async def test_cleaning_up_empties_the_columns_and_everything_still_works(env: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession, Workflow
    from precursor.backend.services.agents import workflow as wf_mod
    from precursor.backend.services.definitions import overlay

    ids = await _seed()
    with TestClient(create_app()) as client:
        client.post("/api/definitions/migrate", json={"acknowledge": True})
        # An agent that got no file in files mode (e.g. an older code path):
        # the cleanup writes it from the database before clearing anything.
        async with SessionLocal() as session:
            late = AgentSession(
                title=f"Late {ids['tag']}", task_prompt="late prompt", status="waiting"
            )
            session.add(late)
            await session.commit()
            late_id = late.id
        preview = client.get("/api/definitions/migration").json()
        assert preview["cleanup"]["missing_files"] == [f"agent 'Late {ids['tag']}'"]

        resp = client.post("/api/definitions/finalize", json={"confirm": True})
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert result["ok"] is True and result["written"] == 1
        assert Path(result["finalized"]["snapshot"]).is_file()

        # Declared by the files, with nothing left in the columns.
        agent = client.get(f"/api/agents/{ids['writer']}").json()
        assert (agent["task_prompt"], agent["approval_policy"]) == ("write it", "manual")
        assert client.get(f"/api/agents/{late_id}").json()["task_prompt"] == "late prompt"
        wf = client.get(f"/api/workflows/{ids['workflow']}").json()
        assert (wf["max_loops"], wf["steps"][1]["kind"]) == (4, "gate")
        assert wf["steps"][1]["agent"]["task_prompt"] == "PASS if fine"

        # No way back from the app, and nothing to export from the database.
        revert = client.post("/api/definitions/revert")
        assert revert.status_code == 409 and "isn't possible" in revert.json()["detail"]
        assert client.post("/api/definitions/export").status_code == 409
        again = client.post("/api/definitions/finalize", json={"confirm": True})
        assert again.status_code == 409

    assert await _raw(
        "agent_sessions", ids["writer"], "task_prompt", "approval_policy", "title"
    ) == (
        "",
        None,
        f"Writer {ids['tag']}",  # names stay, as a search index
    )
    assert await _raw("agent_sessions", ids["vessel"], "task_prompt") == ("",)
    assert await _raw("workflows", ids["workflow"], "max_loops", "name") == (
        3,
        f"Pipeline {ids['tag']}",
    )
    from sqlalchemy import text as sql_text

    async with SessionLocal() as session:
        step_kinds = (
            await session.execute(
                sql_text("select kind, instructions from workflow_steps where workflow_id = :w"),
                {"w": ids["workflow"]},
            )
        ).all()
    assert {tuple(r) for r in step_kinds} == {("task", None)}

    # The workflow still runs from its file, and survives a restart.
    overlay._persisted_source = None
    overlay._finalized = None
    async with SessionLocal() as session:
        await overlay.refresh_source(session)
    assert overlay.files_mode() and overlay.finalized() is not None
    started: list[int] = []

    class _Mgr:
        def start_task(self, agent_id: int, extra_context: str | None = None, *, run_id=None):  # type: ignore[no-untyped-def]
            started.append(agent_id)
            return (agent_id, extra_context)

        def cancel(self, agent_id: int, *, run_id=None):  # type: ignore[no-untyped-def]
            return None

        def enqueue(self, item) -> None:  # type: ignore[no-untyped-def]
            pass

    async with SessionLocal() as session:
        await wf_mod.start_workflow(session, _Mgr(), ids["workflow"])  # type: ignore[arg-type]
    assert started == [ids["writer"]]
    async with SessionLocal() as session:
        wf_row = await session.get(Workflow, ids["workflow"])
        assert wf_row is not None and wf_row.status == "running"


async def test_after_cleanup_a_missing_file_refuses_to_run(env: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.definitions import overlay

    ids = await _seed()
    with TestClient(create_app()) as client:
        client.post("/api/definitions/migrate", json={"acknowledge": True})
        client.post("/api/definitions/finalize", json={"confirm": True})
        path = client.get(f"/api/agents/{ids['writer']}").json()["definition"]["path"]
        (env / path).unlink()
        overlay.invalidate()
        body = client.get(f"/api/agents/{ids['writer']}").json()
        check = client.get("/api/definitions/check").json()
    assert body["definition"]["state"] == "invalid"
    assert "restore the file" in body["definition"]["message"]
    assert check["ok"] is False
    async with SessionLocal() as session:
        row = await session.get(AgentSession, ids["writer"])
        assert row is not None and "missing" in (overlay.definition_error(row) or "")


# --- Gaps closed on the way -------------------------------------------------


async def test_a_blueprint_agent_gets_its_file_in_files_mode(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentBlueprint
    from precursor.backend.services.agents import runtime

    monkeypatch.setattr(runtime, "agents_available", lambda: (True, "test"))
    await _seed()
    async with SessionLocal() as session:
        bp = AgentBlueprint(name=f"BP {_uid()}", task_prompt="from a blueprint")
        session.add(bp)
        await session.commit()
        bp_id = bp.id
    with TestClient(create_app()) as client:
        client.post("/api/definitions/migrate", json={"acknowledge": True})
        resp = client.post(f"/api/agents/blueprints/{bp_id}/instantiate", json={"start": False})
        assert resp.status_code == 201, resp.text
        body = client.get(f"/api/agents/{resp.json()['id']}").json()
    assert body["definition"]["state"] == "file"
    assert _load(env, body["definition"]["path"])["prompt"] == "from a blueprint"


async def test_the_watchdog_reads_the_timeout_from_the_file(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Workflow
    from precursor.backend.services.agents import workflow as wf_mod

    ids = await _seed()
    with TestClient(create_app()) as client:
        client.post("/api/definitions/migrate", json={"acknowledge": True})
        path = client.get(f"/api/workflows/{ids['workflow']}").json()["definition"]["path"]
    _write(env, path, {**_load(env, path), "step_timeout_seconds": 30})
    async with SessionLocal() as session:
        wf = await session.get(Workflow, ids["workflow"])
        assert wf is not None
        wf.status, wf.current_run_id = "running", 0
        await session.commit()
    # Only the file has a timeout; the column never did.
    assert await _raw("workflows", ids["workflow"], "step_timeout_seconds") == (None,)

    considered: list[int] = []
    real_lock = wf_mod._workflow_lock

    def spy(workflow_id: int):  # type: ignore[no-untyped-def]
        considered.append(workflow_id)
        return real_lock(workflow_id)

    monkeypatch.setattr(wf_mod, "_workflow_lock", spy)
    async with SessionLocal() as session:
        await wf_mod.sweep_stalled_steps(session, object())  # type: ignore[arg-type]
    assert considered == [ids["workflow"]]


async def test_search_finds_prompts_that_live_in_files(env: Path) -> None:
    ids = await _seed()
    with TestClient(create_app()) as client:
        client.post("/api/definitions/migrate", json={"acknowledge": True})
        path = client.get(f"/api/agents/{ids['writer']}").json()["definition"]["path"]
        _write(env, path, {**_load(env, path), "prompt": "count the zanzibar penguins"})
        hits = client.get("/api/search", params={"q": "zanzibar"}).json()
    agents = [h for h in hits["results"] if h["section"] == "agents"]
    assert [(h["entity_id"], h["field"]) for h in agents] == [(ids["writer"], "prompt")]


async def test_names_edited_in_files_reach_the_searchable_column(env: Path) -> None:
    ids = await _seed()
    with TestClient(create_app()) as client:
        client.post("/api/definitions/migrate", json={"acknowledge": True})
        path = client.get(f"/api/agents/{ids['writer']}").json()["definition"]["path"]
        _write(env, path, {**_load(env, path), "title": "Renamed on disk"})
        client.get("/api/agents")
    assert await _raw("agent_sessions", ids["writer"], "title") == ("Renamed on disk",)


# --- Status (the homes' invitation banner) ----------------------------------


async def test_the_status_counts_what_is_left_to_migrate(env: Path) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession, Workflow

    ids = await _seed()
    async with SessionLocal() as session:
        archived = AgentSession(
            title="Old", task_prompt="p", status="waiting", archived_at=datetime.now(UTC)
        )
        session.add(archived)
        await session.commit()
    with TestClient(create_app()) as client:
        before = client.get("/api/definitions/status").json()
        # Listed, active ones only: the step's private agent and the archived
        # agent aren't "your agents".
        assert before == {"stage": "database", "forced": False, "agents": 2, "workflows": 1}

        client.post("/api/definitions/migrate", json={"acknowledge": True})
        assert client.get("/api/definitions/status").json()["agents"] == 0

        # A row that somehow got no file after the switch: a leftover to write.
        async with SessionLocal() as session:
            session.add(
                AgentSession(title=f"Stray {ids['tag']}", task_prompt="p", status="waiting")
            )
            await session.commit()
        leftover = client.get("/api/definitions/status").json()
        assert (leftover["stage"], leftover["agents"], leftover["workflows"]) == ("files", 1, 0)
        assert client.post("/api/definitions/export").status_code == 200
        assert client.get("/api/definitions/status").json()["agents"] == 0

        client.post("/api/definitions/finalize", json={"confirm": True})
        done = client.get("/api/definitions/status").json()
    assert (done["stage"], done["agents"], done["workflows"]) == ("finalized", 0, 0)
    async with SessionLocal() as session:
        assert await session.get(Workflow, ids["workflow"]) is not None
