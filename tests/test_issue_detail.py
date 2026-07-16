"""GitHub issue-detail endpoint tests (kanban card preview).

The GitHub REST calls are mocked; these tests cover response shaping and the
linked-topic resolution used by the kanban preview modal.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.routers import github as github_router


class _FakeClient:
    def __init__(self, *, token: str) -> None:
        self.token = token

    async def aclose(self) -> None:
        return None

    async def get_issue(self, repo: str, number: int) -> dict[str, Any]:
        return {
            "number": number,
            "title": f"Issue {number}",
            "state": "open",
            "url": f"https://github.com/{repo}/issues/{number}",
            "body": "Some **body**",
            "labels": [{"name": "bug", "color": "d73a4a"}],
            "updated_at": "2024-01-01T00:00:00Z",
        }

    async def list_issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        return [{"id": 1, "user": "octocat", "body": "first", "updated_at": "2024-01-02T00:00:00Z"}]


@pytest.fixture()
def _mock_github(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _repo(_session: Any) -> str:
        return "acme/app"

    async def _enabled(_session: Any) -> bool:
        return True

    async def _token(_session: Any) -> str:
        return "tok"

    monkeypatch.setattr(github_router, "GitHubClient", _FakeClient)
    monkeypatch.setattr(github_router, "resolve_global_github_repo", _repo)
    monkeypatch.setattr(github_router, "resolve_issue_associations_enabled", _enabled)
    monkeypatch.setattr(github_router, "resolve_github_token", _token)


def test_issue_detail_shape(_mock_github: None) -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/github/issues/12?repo=acme/app")
        assert r.status_code == 200
        d = r.json()
        assert d["number"] == 12
        assert d["labels"][0]["name"] == "bug"
        assert d["comments"][0]["user"] == "octocat"
        assert d["linked_topic_id"] is None


def test_issue_detail_resolves_linked_topic(_mock_github: None) -> None:
    app = create_app()
    with TestClient(app) as client:
        created = client.post(
            "/api/topics",
            json={
                "title": "Fix the thing",
                "github_repo": "acme/app",
                "github_issue_number": 12,
            },
        )
        assert created.status_code in (200, 201)
        topic_id = created.json()["id"]

        r = client.get("/api/github/issues/12?repo=acme/app")
        assert r.status_code == 200
        d = r.json()
        assert d["linked_topic_id"] == topic_id
        assert d["linked_topic_title"] == "Fix the thing"

        # A different repo must not match the topic.
        r2 = client.get("/api/github/issues/12?repo=other/repo")
        assert r2.json()["linked_topic_id"] is None
