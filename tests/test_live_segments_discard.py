"""Batch discard of live transcript phrases."""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.main import create_app


def _seed(client: TestClient, title: str, texts: list[str]) -> tuple[int, list[int]]:
    sid = client.post("/api/live", json={"title": title}).json()["id"]
    ids = [client.post(f"/api/live/{sid}/segments", json={"text": t}).json()["id"] for t in texts]
    return sid, ids


def test_discard_removes_only_the_given_phrases() -> None:
    with TestClient(create_app()) as client:
        sid, ids = _seed(client, "Forgot to stop", ["one", "two", "three", "four"])

        res = client.post(f"/api/live/{sid}/segments/discard", json={"ids": ids[2:]})
        assert res.status_code == 200
        assert res.json() == {"deleted": 2}

        rows = client.get(f"/api/live/{sid}/segments").json()
        assert [r["text"] for r in rows] == ["one", "two"]


def test_discard_ignores_other_sessions_and_unknown_ids() -> None:
    with TestClient(create_app()) as client:
        sid_a, ids_a = _seed(client, "A", ["keep me"])
        sid_b, ids_b = _seed(client, "B", ["drop me"])

        res = client.post(
            f"/api/live/{sid_b}/segments/discard", json={"ids": [*ids_a, *ids_b, 999_999]}
        )
        assert res.json() == {"deleted": 1}
        assert [r["text"] for r in client.get(f"/api/live/{sid_a}/segments").json()] == ["keep me"]
        assert client.get(f"/api/live/{sid_b}/segments").json() == []


def test_discard_validates_input() -> None:
    with TestClient(create_app()) as client:
        sid, _ = _seed(client, "V", ["x"])
        assert client.post(f"/api/live/{sid}/segments/discard", json={"ids": []}).status_code == 422
        assert (
            client.post("/api/live/999999/segments/discard", json={"ids": [1]}).status_code == 404
        )
