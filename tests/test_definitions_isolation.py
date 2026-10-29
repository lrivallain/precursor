"""Migration fixtures must not inherit another install's irreversible cleanup."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from tests.definitions_support import (
    isolated_migration_database,
    mark_database,
    restore_database,
)


@pytest.mark.parametrize("previous_stage", ["migrated", "finalized", "fresh"])
async def test_migration_isolated_from_a_previous_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, previous_stage: str
) -> None:
    from precursor.backend.config import get_settings
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AppSetting
    from precursor.backend.services.definitions import overlay

    original = await mark_database()
    monkeypatch.setattr(get_settings(), "definitions_source", "database")
    monkeypatch.setattr(get_settings(), "definitions_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "definitions_dir_override", str(tmp_path))
    record = {"at": "2026-01-01T00:00:00+00:00", "fresh": previous_stage == "fresh"}
    incoming = {
        "definitions_source": "files",
        "definitions_migrated": {"at": record["at"], "created": 3},
        "definitions_roles_filed": True,
        "definitions_finalized": None if previous_stage == "migrated" else record,
    }
    try:
        async with SessionLocal() as session:
            for key, value in incoming.items():
                await session.merge(AppSetting(key=key, value=json.dumps(value)))
            await session.commit()
            await overlay.refresh_source(session)

        # Two complete migrations in one worker: both must start unfinalized,
        # even though the saved incoming state belongs to a different install.
        for _ in range(2):
            async with isolated_migration_database():
                assert overlay.finalized() is None
                assert overlay.files_mode() is False
                async with SessionLocal() as session:
                    for key in incoming.keys() - {"definitions_source"}:
                        assert await session.get(AppSetting, key) is None
                with TestClient(create_app()) as client:
                    assert client.get("/api/definitions/migration").json()["stage"] == "database"
                    migrated = client.post("/api/definitions/migrate", json={"acknowledge": True})
                    assert migrated.status_code == 200, migrated.text
                    cleaned = client.post("/api/definitions/finalize", json={"confirm": True})
                    assert cleaned.status_code == 200, cleaned.text
                    again = client.post("/api/definitions/finalize", json={"confirm": True})
                    assert again.status_code == 409
                    assert again.json()["detail"] == "The database was already cleaned up"
            async with SessionLocal() as session:
                for key, value in incoming.items():
                    row = await session.get(AppSetting, key)
                    assert row is not None and json.loads(row.value) == value
            assert overlay.finalized() == incoming["definitions_finalized"]
            shutil.rmtree(tmp_path)
            tmp_path.mkdir()
    finally:
        await restore_database(original)
