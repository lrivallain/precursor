"""Roles as definition files: the checker rules, the API in files mode, the wizard."""

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
from sqlalchemy import select

from precursor.backend.main import create_app
from precursor.backend.services.definitions.checker import check_definitions
from precursor.backend.services.definitions.loader import load_definitions
from tests.definitions_support import mark_database, restore_database, wipe_agents_and_workflows


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
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AppSetting
    from precursor.backend.services.definitions import overlay

    mark = await mark_database()
    # The migration refuses while any workflow is mid-run, and the shared DB holds other modules' leftovers.
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
