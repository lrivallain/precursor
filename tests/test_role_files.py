"""Roles as definition files: the checker rules, the API in files mode, the wizard."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select

from precursor.backend.main import create_app
from precursor.backend.services.definitions.checker import check_definitions
from precursor.backend.services.definitions.loader import load_definitions
from tests.definitions_support import (
    isolated_migration_database,
    mark_database,
    restore_database,
)


def _uid() -> str:
    return "r" + uuid.uuid4().hex[:8]


def _role_doc(ident: str, name: str, prompt: str = "Be brief.") -> dict[str, Any]:
    return {"kind": "role", "id": ident, "name": name, "prompt": prompt}


def _put(root: Path, rel: str, doc: dict[str, Any]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


# --- The checker -------------------------------------------------------------


def _issues(root: Path) -> list[tuple[str, str]]:
    return [(i.severity, i.message) for i in check_definitions(load_definitions(root))]


def test_a_valid_folder_of_roles_is_clean(tmp_path: Path) -> None:
    _put(tmp_path, "roles/default.role.yaml", _role_doc("default", "default", ""))
    _put(tmp_path, "roles/terse.role.yaml", _role_doc("terse", "Terse"))
    assert _issues(tmp_path) == []


def test_two_roles_with_the_same_name_are_both_errors(tmp_path: Path) -> None:
    _put(tmp_path, "roles/default.role.yaml", _role_doc("default", "default", ""))
    _put(tmp_path, "roles/a.role.yaml", _role_doc("a", "Terse"))
    _put(tmp_path, "roles/b.role.yaml", _role_doc("b", "terse"))
    errors = [m for s, m in _issues(tmp_path) if s == "error"]
    assert len(errors) == 2 and all("has this name" in m for m in errors)


def test_the_default_name_and_id_go_together(tmp_path: Path) -> None:
    _put(tmp_path, "roles/x.role.yaml", _role_doc("x", "default", ""))
    _put(tmp_path, "roles/y.role.yaml", _role_doc("default", "Other", ""))
    errors = [m for s, m in _issues(tmp_path) if s == "error"]
    assert any("built-in role's name" in m for m in errors)
    assert any("reserved" in m for m in errors)


def test_a_missing_default_file_is_only_a_warning(tmp_path: Path) -> None:
    _put(tmp_path, "roles/terse.role.yaml", _role_doc("terse", "Terse"))
    assert [s for s, _ in _issues(tmp_path)] == ["warning"]


def test_an_agent_must_name_a_role_the_folder_has(tmp_path: Path) -> None:
    _put(tmp_path, "roles/default.role.yaml", _role_doc("default", "default", ""))
    _put(tmp_path, "roles/terse.role.yaml", _role_doc("terse", "Terse"))
    _put(
        tmp_path,
        "agents/ok.agent.yaml",
        {"kind": "agent", "id": "ok", "title": "Ok", "role": "TERSE"},
    )
    _put(
        tmp_path,
        "agents/bad.agent.yaml",
        {"kind": "agent", "id": "bad", "title": "Bad", "role": "Nobody"},
    )
    problems = _issues(tmp_path)
    assert len(problems) == 1 and "Nobody" in problems[0][1]


# --- The API in files mode ---------------------------------------------------


@pytest.fixture
async def files_mode(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Path]:
    from precursor.backend.config import get_settings
    from precursor.backend.services.definitions import overlay

    mark = await mark_database()
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


def test_the_default_role_file_is_seeded_at_startup(files_mode: Path) -> None:
    with TestClient(create_app()) as client:
        roles = {r["name"]: r for r in client.get("/api/roles").json()}
    assert roles["default"]["is_default"] is True
    assert (files_mode / "roles" / "default.role.yaml").is_file()


def test_the_role_api_edits_the_files(files_mode: Path) -> None:
    name = f"Persona {_uid()}"
    with TestClient(create_app()) as client:
        created = client.post("/api/roles", json={"name": name, "system_prompt": "First."})
        assert created.status_code == 201, created.text
        path = files_mode / created.json()["path"]
        assert yaml.safe_load(path.read_text())["prompt"].strip() == "First."

        saved = client.patch(f"/api/roles/{name}", json={"system_prompt": "Second."})
        assert saved.status_code == 200
        assert yaml.safe_load(path.read_text())["prompt"].strip() == "Second."

        assert client.patch(f"/api/roles/{name}", json={"name": "Other"}).status_code == 400
        assert client.delete("/api/roles/default").status_code == 400

        assert client.delete(f"/api/roles/{name}").status_code == 204
        assert not path.exists()


def test_deleting_a_role_file_clears_what_referred_to_it(files_mode: Path) -> None:
    name = f"Persona {_uid()}"
    with TestClient(create_app()) as client:
        client.post("/api/roles", json={"name": name, "system_prompt": "x"})
        topic = client.post("/api/topics", json={"title": "t", "role": name}).json()
        assert topic["role"] == name
        assert client.delete(f"/api/roles/{name}").status_code == 204
        assert client.get(f"/api/topics/{topic['id']}").json()["role"] is None


async def test_a_name_no_file_defines_falls_back_to_the_default(
    files_mode: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.services import roles as roles_service

    _put(files_mode, "roles/default.role.yaml", _role_doc("default", "default", "House style."))
    async with SessionLocal() as session:
        with caplog.at_level("WARNING"):
            prompt = await roles_service.resolve_role_prompt(session, "Ghost")
    assert prompt == "House style."
    assert "Ghost" in caplog.text


def test_ensure_default_role_file_leaves_an_existing_one_alone(files_mode: Path) -> None:
    from precursor.backend.services import roles as roles_service
    from precursor.backend.services.definitions import overlay

    _put(files_mode, "roles/mine.role.yaml", _role_doc("default", "Default", "Mine."))
    overlay.invalidate()
    roles_service.ensure_default_role_file()
    assert [p.name for p in (files_mode / "roles").iterdir()] == ["mine.role.yaml"]


# --- The wizard --------------------------------------------------------------


@pytest.fixture
async def database_mode(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Path]:
    from precursor.backend.config import get_settings
    from precursor.backend.services.definitions import overlay

    async with isolated_migration_database():
        settings = get_settings()
        monkeypatch.setattr(settings, "definitions_source", "database")
        root = Path(settings.definitions_dir)
        shutil.rmtree(root, ignore_errors=True)
        overlay.invalidate()
        try:
            yield root
        finally:
            overlay.invalidate()
            overlay._pins.clear()
            shutil.rmtree(root, ignore_errors=True)


async def _add_role(name: str, prompt: str) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Role

    async with SessionLocal() as session:
        session.add(Role(name=name, system_prompt=prompt))
        await session.commit()


async def _role_prompt(name: str) -> str | None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Role

    async with SessionLocal() as session:
        row = await session.scalar(select(Role).where(Role.name == name))
        return None if row is None else row.system_prompt


@pytest.mark.parametrize("source", ["database", "files"])
async def test_deletion_clears_agent_and_workflow_roles_durably(
    database_mode: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    from precursor.backend.config import get_settings
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession, Workflow
    from precursor.backend.services.definitions import overlay

    monkeypatch.setattr(get_settings(), "definitions_source", source)
    name = f"Researcher {_uid()}"
    agent_key, workflow_key = _uid(), _uid()
    with TestClient(create_app()) as client:
        assert client.post("/api/roles", json={"name": name}).status_code == 201
        async with SessionLocal() as session:
            agent = AgentSession(
                title="Task",
                task_prompt="seed",
                status="idle",
                export_id=agent_key,
                role=name.upper() if source == "database" else None,
                accepted_permissions="{}",
            )
            workflow = Workflow(
                name="Pipeline",
                export_id=workflow_key,
                role=name.upper() if source == "database" else None,
                accepted_permissions="{}",
            )
            session.add_all([agent, workflow])
            await session.commit()
            agent_id, workflow_id = agent.id, workflow.id

        documents = {
            "agents/research.agent.yaml": {
                "kind": "agent",
                "id": agent_key,
                "title": "Task",
                "prompt": "Keep this.",
                "role": name.upper(),
                "capabilities": {"memory": False},
            },
            "workflows/research.workflow.yaml": {
                "kind": "workflow",
                "id": workflow_key,
                "name": "Pipeline",
                "role": name.upper(),
                "steps": [{"key": "check", "kind": "inline", "prompt": "Keep this step."}],
            },
            "agents/unadopted.agent.yaml": {
                "kind": "agent",
                "id": _uid(),
                "title": "Not adopted",
                "role": name,
            },
            "workflows/unadopted.workflow.yaml": {
                "kind": "workflow",
                "id": _uid(),
                "name": "Not adopted",
                "role": name,
            },
        }
        for rel, doc in documents.items():
            _put(database_mode, rel, doc)
        if source == "files":
            _put(
                database_mode,
                "agents/other.agent.yaml",
                {
                    "kind": "agent",
                    "id": _uid(),
                    "title": "Other",
                    "role": "Other persona",
                },
            )
            untouched = (database_mode / "agents/other.agent.yaml").read_bytes()
            overlay.invalidate()

        assert client.get(f"/api/agents/{agent_id}").json()["role"] == name.upper()
        assert client.get(f"/api/workflows/{workflow_id}").json()["role"] == name.upper()
        deleted = client.delete(f"/api/roles/{name.upper()}")
        assert deleted.status_code == 204, deleted.text
        overlay.invalidate()
        assert client.get(f"/api/agents/{agent_id}").json()["role"] is None
        assert client.get(f"/api/workflows/{workflow_id}").json()["role"] is None

        for rel, doc in documents.items():
            expected = (
                {key: value for key, value in doc.items() if key != "role"}
                if source == "files"
                else doc
            )
            assert yaml.safe_load((database_mode / rel).read_text()) == expected
        if source == "files":
            assert (database_mode / "agents/other.agent.yaml").read_bytes() == untouched
        async with SessionLocal() as session:
            agent = await session.get(AgentSession, agent_id)
            workflow = await session.get(Workflow, workflow_id)
            assert agent is not None and agent.accepted_permissions == "{}"
            assert workflow is not None and workflow.accepted_permissions == "{}"

    with TestClient(create_app()) as client:
        assert client.get(f"/api/agents/{agent_id}").json()["role"] is None
        assert client.get(f"/api/workflows/{workflow_id}").json()["role"] is None
        # Reusing a deleted name must not silently reassign old discussions.
        assert client.post("/api/roles", json={"name": name}).status_code == 201
        assert client.get(f"/api/agents/{agent_id}").json()["role"] is None


@pytest.mark.parametrize("problem", ["invalid", "ambiguous", "unwritable", "changed"])
def test_unsafe_definition_cleanup_keeps_the_role(
    files_mode: Path, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    from precursor.backend.services.definitions import overlay, writer
    from precursor.backend.services.definitions.loader import LoadedFile

    name = f"Researcher {_uid()}"
    with TestClient(create_app()) as client:
        role = client.post("/api/roles", json={"name": name}).json()
        doc = {"kind": "agent", "id": _uid(), "title": "Task", "role": name}
        _put(files_mode, "agents/research.agent.yaml", doc)
        if problem == "invalid":
            doc["unknown"] = True
            _put(files_mode, "agents/research.agent.yaml", doc)
        elif problem == "ambiguous":
            _put(files_mode, "agents/copy.agent.yaml", doc)
        elif problem == "unwritable":

            def refuse_write(*args: Any, **kwargs: Any) -> None:
                raise PermissionError("test: definition is read-only")

            monkeypatch.setattr(writer, "_write_atomic", refuse_write)
        else:
            read_unchanged = writer._read_unchanged
            raced = False

            def change_after_preflight(linked: LoadedFile) -> bytes:
                nonlocal raced
                data = read_unchanged(linked)
                if not raced:
                    raced = True
                    (files_mode / linked.path).write_bytes(data + b"# Concurrent edit.\n")
                return data

            monkeypatch.setattr(writer, "_read_unchanged", change_after_preflight)
        if problem in ("invalid", "ambiguous"):
            _put(
                files_mode,
                "agents/first.agent.yaml",
                {
                    "kind": "agent",
                    "id": _uid(),
                    "title": "First",
                    "role": name,
                },
            )
            first = (files_mode / "agents/first.agent.yaml").read_bytes()
        overlay.invalidate()
        before = (files_mode / "agents/research.agent.yaml").read_bytes()
        deleted = client.delete(f"/api/roles/{name}")
        assert deleted.status_code == 409, deleted.text
        assert "research.agent.yaml" in deleted.json()["detail"]
        assert client.get(f"/api/roles/{name}").status_code == 200
        assert (files_mode / role["path"]).is_file()
        expected = before + b"# Concurrent edit.\n" if problem == "changed" else before
        assert (files_mode / "agents/research.agent.yaml").read_bytes() == expected
        if problem in ("invalid", "ambiguous"):
            assert (files_mode / "agents/first.agent.yaml").read_bytes() == first


@pytest.mark.parametrize("file_role_changed", [False, True])
async def test_deleting_role_updates_only_the_role_in_a_running_workflow_snapshot(
    files_mode: Path, file_role_changed: bool
) -> None:
    import hashlib

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Workflow, WorkflowRun
    from precursor.backend.schemas.definitions import WorkflowDefinition
    from precursor.backend.services.definitions import anchors, overlay
    from precursor.backend.services.definitions.exporter import render_document

    name, ident = f"Researcher {_uid()}", _uid()
    rel = "workflows/pinned.workflow.yaml"
    original = {
        "kind": "workflow",
        "id": ident,
        "name": "Pinned",
        "role": name.upper(),
        "steps": [{"key": "original", "kind": "inline", "prompt": "Original step."}],
    }
    snapshot = render_document(original)
    digest = hashlib.sha256(snapshot.encode()).hexdigest()
    with TestClient(create_app()) as client:
        assert client.post("/api/roles", json={"name": name}).status_code == 201
        _put(files_mode, rel, original)
        overlay.invalidate()
        async with SessionLocal() as session:
            workflow = Workflow(
                name="Pinned",
                export_id=ident,
                status="paused",
                accepted_permissions="{}",
            )
            session.add(workflow)
            await session.flush()
            run = WorkflowRun(
                workflow_id=workflow.id,
                run_number=1,
                status="paused",
                definition_path=rel,
                definition_hash=digest,
                definition_snapshot=snapshot,
            )
            session.add(run)
            await session.flush()
            workflow.current_run_id = run.id
            await session.commit()
            workflow_id, run_id = workflow.id, run.id
        overlay.pin_run(ident, run_id, WorkflowDefinition.model_validate(original), rel, digest)
        changed = {
            **original,
            "description": "For the next run.",
            "steps": [{"key": "next", "kind": "inline", "prompt": "Next step."}],
        }
        if file_role_changed:
            changed["role"] = "Another persona"
        _put(files_mode, rel, changed)
        overlay.invalidate()

        deleted = client.delete(f"/api/roles/{name}")
        assert deleted.status_code == 204, deleted.text
        expected_file = dict(changed)
        if not file_role_changed:
            expected_file.pop("role")
        assert yaml.safe_load((files_mode / rel).read_text()) == expected_file

        async with SessionLocal() as session:
            row = await session.get(Workflow, workflow_id)
            run = await session.get(WorkflowRun, run_id)
            assert row is not None and row.role is None
            assert row.accepted_permissions == "{}"
            assert run is not None and run.definition_snapshot is not None
            saved = yaml.safe_load(run.definition_snapshot)
            assert saved["steps"] == original["steps"]
            assert saved.get("role") is None
            assert "description" not in saved
            assert (
                run.definition_hash == hashlib.sha256(run.definition_snapshot.encode()).hexdigest()
            )
        overlay._pins.clear()
        async with SessionLocal() as session:
            assert await anchors.restore_pins(session) >= 1
            row = await session.get(Workflow, workflow_id)
            assert row is not None and row.role is None


async def _roles_filed_marker() -> bool:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AppSetting

    async with SessionLocal() as session:
        return await session.get(AppSetting, "definitions_roles_filed") is not None


async def test_migrating_writes_the_roles_and_reverting_brings_them_back(
    database_mode: Path,
) -> None:
    name = f"Persona {_uid()}"
    await _add_role(name, "Original.")
    with TestClient(create_app()) as client:
        preview = client.get("/api/definitions/migration").json()
        assert preview["roles"] >= 1

        done = client.post("/api/definitions/migrate", json={"acknowledge": True})
        assert done.status_code == 200, done.text
        assert done.json()["roles"] >= 1
        files = [yaml.safe_load(p.read_text()) for p in (database_mode / "roles").glob("*.yaml")]
        by_name = {f["name"]: f for f in files}
        assert by_name[name]["prompt"].strip() == "Original."
        assert "default" in by_name
        # Filed by the migration itself, so a restart won't file them again.
        assert await _roles_filed_marker() is True

        # Edited as a file, the new prompt is what reverting hands back.
        path = next(p for p in (database_mode / "roles").glob("*.yaml") if name in p.read_text())
        doc = yaml.safe_load(path.read_text())
        doc["prompt"] = "Edited as a file."
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

        reverted = client.post("/api/definitions/revert")
        assert reverted.status_code == 200, reverted.text
        assert reverted.json()["roles"] >= 1
    assert await _role_prompt(name) == "Edited as a file."
    # Back on the table: roles edited there must be filed if files mode returns.
    assert await _roles_filed_marker() is False


async def test_an_install_migrated_before_roles_were_files_can_still_revert(
    database_mode: Path,
) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.services.definitions import migration, overlay

    name = f"Persona {_uid()}"
    await _add_role(name, "Held in the table.")
    async with SessionLocal() as session:
        await overlay.persist_source(session, "files")
    with TestClient(create_app()) as client:
        assert client.get("/api/definitions/migration").json()["stage"] == "files"
        async with SessionLocal() as session:
            assert await migration.backfill_role_files(session) >= 1

        reverted = client.post("/api/definitions/revert")
        assert reverted.status_code == 200, reverted.text
    assert (await _role_prompt(name) or "").strip() == "Held in the table."


async def test_an_install_migrated_before_roles_were_files_can_still_clean_up(
    database_mode: Path,
) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.services.definitions import overlay

    name = f"Persona {_uid()}"
    await _add_role(name, "Held in the table.")
    async with SessionLocal() as session:
        await overlay.persist_source(session, "files")
    with TestClient(create_app()) as client:
        cleaned = client.post("/api/definitions/finalize", json={"confirm": True})
        assert cleaned.status_code == 200, cleaned.text
    assert any(name in p.read_text() for p in (database_mode / "roles").glob("*.yaml"))


async def test_cleaning_up_blanks_the_role_prompts_in_the_table(database_mode: Path) -> None:
    name = f"Persona {_uid()}"
    await _add_role(name, "Kept in the file.")
    with TestClient(create_app()) as client:
        assert (
            client.post("/api/definitions/migrate", json={"acknowledge": True}).status_code == 200
        )
        cleaned = client.post("/api/definitions/finalize", json={"confirm": True})
        assert cleaned.status_code == 200, cleaned.text
        assert cleaned.json()["finalized"]["roles"] >= 1
        assert (
            next(r["system_prompt"] for r in client.get("/api/roles").json() if r["name"] == name)
            == "Kept in the file.\n"
        )
    assert await _role_prompt(name) == ""
