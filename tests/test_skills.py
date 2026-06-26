"""Skill persistence tests.

Skills now live as ``<copilot_home>/skills/<name>/SKILL.md`` files; the DB only
tracks enablement (plus transitional legacy rows). These tests exercise the
file/DB reconciliation through the API and the service layer.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import Skill


def _skills_root() -> Path:
    return Path(get_settings().skills_dir)


def _write_external_skill(name: str, description: str, body: str) -> Path:
    """Simulate a skill authored by another tool (Copilot CLI, etc.)."""
    path = _skills_root() / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


async def _insert_legacy(name: str, description: str, instructions: str) -> None:
    async with SessionLocal() as session:
        session.add(
            Skill(
                name=name,
                description=description,
                instructions=instructions,
                migrated=False,
                enabled=False,
            )
        )
        await session.commit()


def _by_name(items: list[dict], name: str) -> dict | None:
    return next((i for i in items if i["name"] == name), None)


# ---------------------------------------------------------------------------
# Creating a skill writes a shared SKILL.md file
# ---------------------------------------------------------------------------
def test_create_writes_file_and_is_enabled() -> None:
    app = create_app()
    with TestClient(app) as client:
        resp = client.post(
            "/api/skills",
            json={
                "name": "to-en",
                "description": "Translate to English",
                "instructions": "Translate the input to English.",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["enabled"] is True
        assert body["active"] is True
        assert body["legacy"] is False

        skill_md = _skills_root() / "to-en" / "SKILL.md"
        assert skill_md.is_file()
        text = skill_md.read_text(encoding="utf-8")
        assert text.startswith("---")
        assert "name: to-en" in text
        assert "Translate the input to English." in text


def test_create_rejects_reserved_and_duplicate() -> None:
    app = create_app()
    with TestClient(app) as client:
        assert (
            client.post("/api/skills", json={"name": "notes", "instructions": "x"}).status_code
            == 400
        )
        assert (
            client.post("/api/skills", json={"name": "dup", "instructions": "x"}).status_code == 201
        )
        assert (
            client.post("/api/skills", json={"name": "dup", "instructions": "y"}).status_code == 400
        )


# ---------------------------------------------------------------------------
# Discovered (external) skills default to disabled, opt-in to enable
# ---------------------------------------------------------------------------
def test_discovered_skill_disabled_until_enabled() -> None:
    _write_external_skill("vbd-lookup", "Find VBDs", "Search for VBD offerings.")
    app = create_app()
    with TestClient(app) as client:
        skills = client.get("/api/skills").json()
        entry = _by_name(skills, "vbd-lookup")
        assert entry is not None
        assert entry["enabled"] is False
        assert entry["active"] is False
        assert entry["legacy"] is False
        # Instructions are read from the file.
        assert "Search for VBD offerings." in entry["instructions"]

        enabled = client.patch("/api/skills/vbd-lookup", json={"enabled": True}).json()
        assert enabled["enabled"] is True
        assert enabled["active"] is True


def test_enablement_lost_when_file_deleted() -> None:
    path = _write_external_skill("ghost", "Spooky", "Boo.")
    app = create_app()
    with TestClient(app) as client:
        client.patch("/api/skills/ghost", json={"enabled": True})
        # The enablement is now tracked by a DB row.
        # Delete the underlying file as another tool might.
        path.unlink()
        (path.parent).rmdir()

        skills = client.get("/api/skills").json()
        assert _by_name(skills, "ghost") is None

        # The orphaned enablement row was dropped during reconciliation.
        async def _count() -> int:
            async with SessionLocal() as session:
                from sqlalchemy import func, select

                return (
                    await session.execute(
                        select(func.count()).select_from(Skill).where(Skill.name == "ghost")
                    )
                ).scalar_one()

        assert asyncio.run(_count()) == 0


# ---------------------------------------------------------------------------
# Legacy DB skills keep working and can be migrated
# ---------------------------------------------------------------------------
def test_legacy_skill_active_then_migrate() -> None:
    app = create_app()
    with TestClient(app) as client:
        asyncio.run(_insert_legacy("oldie", "Legacy one", "Do the legacy thing."))

        skills = client.get("/api/skills").json()
        entry = _by_name(skills, "oldie")
        assert entry is not None
        assert entry["legacy"] is True
        assert entry["active"] is True  # legacy skills stay active until migrated
        assert "Do the legacy thing." in entry["instructions"]

        # No file should exist yet.
        assert not (_skills_root() / "oldie" / "SKILL.md").exists()

        migrated = client.post("/api/skills/oldie/migrate").json()
        assert migrated["legacy"] is False
        assert migrated["enabled"] is True
        assert migrated["active"] is True

        # File now exists, DB content cleared but enablement row kept.
        assert (_skills_root() / "oldie" / "SKILL.md").is_file()

        async def _row() -> Skill | None:
            async with SessionLocal() as session:
                from sqlalchemy import select

                return (
                    await session.execute(select(Skill).where(Skill.name == "oldie"))
                ).scalar_one_or_none()

        row = asyncio.run(_row())
        assert row is not None
        assert row.migrated is True
        assert row.enabled is True
        assert (row.instructions or "") == ""


def test_export_renders_frontmatter() -> None:
    app = create_app()
    with TestClient(app) as client:
        client.post(
            "/api/skills",
            json={"name": "expo", "description": "Export me", "instructions": "Body here."},
        )
        resp = client.get("/api/skills/expo/export")
        assert resp.status_code == 200
        assert resp.text.startswith("---")
        assert "name: expo" in resp.text
        assert "Body here." in resp.text
        assert "expo.SKILL.md" in resp.headers["content-disposition"]


def test_enabling_external_skill_preserves_file() -> None:
    path = _write_external_skill("keepme", "Untouched", "Body stays exactly.")
    original = path.read_text(encoding="utf-8")
    app = create_app()
    with TestClient(app) as client:
        client.patch("/api/skills/keepme", json={"enabled": True})
        # Pure enablement toggle must not rewrite/reformat the shared file.
        assert path.read_text(encoding="utf-8") == original


def test_long_description_and_large_body_accepted() -> None:
    """File-backed skills (incl. external ones) can exceed the old 255/20k caps."""
    long_desc = "x" * 1200  # real Copilot skills reach ~1 KB descriptions
    big_body = "# Big skill\n\n" + ("lorem ipsum " * 4000)  # ~48 KB body
    app = create_app()
    with TestClient(app) as client:
        created = client.post(
            "/api/skills",
            json={"name": "biggy", "description": long_desc, "instructions": big_body},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["description"] == long_desc
        assert body["instructions"].strip().endswith("lorem ipsum".strip())

        # Editing a discovered external skill with a long description must save.
        _write_external_skill("ext-long", "short", "Body.")
        edited = client.patch(
            "/api/skills/ext-long",
            json={"description": long_desc, "instructions": "New body."},
        )
        assert edited.status_code == 200
        assert edited.json()["description"] == long_desc


def test_update_file_skill_rewrites_file() -> None:
    app = create_app()
    with TestClient(app) as client:
        client.post(
            "/api/skills",
            json={"name": "edit-me", "instructions": "Original."},
        )
        client.patch("/api/skills/edit-me", json={"instructions": "Updated body."})
        text = (_skills_root() / "edit-me" / "SKILL.md").read_text(encoding="utf-8")
        assert "Updated body." in text
        assert "Original." not in text
