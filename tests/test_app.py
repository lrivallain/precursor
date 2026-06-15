"""Smoke tests for the FastAPI app — ensures imports + lifespan wire up cleanly."""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.main import create_app


def test_create_app_and_health() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert isinstance(body.get("version"), str) and body["version"]


def test_topics_empty_tree() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/topics/tree")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
