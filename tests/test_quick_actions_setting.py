"""The quick-actions visibility preference round-trips and is on by default."""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.main import create_app


def test_quick_actions_are_shown_by_default_and_the_choice_persists() -> None:
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/settings").json()["show_quick_actions"] is True

        updated = client.put("/api/settings", json={"show_quick_actions": False})
        assert updated.json()["show_quick_actions"] is False
        assert client.get("/api/settings").json()["show_quick_actions"] is False

        client.put("/api/settings", json={"show_quick_actions": True})
        assert client.get("/api/settings").json()["show_quick_actions"] is True
