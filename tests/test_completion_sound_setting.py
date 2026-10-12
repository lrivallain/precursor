"""The completion-sound preference round-trips and is off on a fresh install."""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.main import create_app


def test_completion_sound_is_off_by_default_and_persists() -> None:
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/settings").json()["completion_sound_enabled"] is False

        updated = client.put("/api/settings", json={"completion_sound_enabled": True})
        assert updated.json()["completion_sound_enabled"] is True
        assert client.get("/api/settings").json()["completion_sound_enabled"] is True

        client.put("/api/settings", json={"completion_sound_enabled": False})
        assert client.get("/api/settings").json()["completion_sound_enabled"] is False
