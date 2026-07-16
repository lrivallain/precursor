"""GitHub Projects v2 board API tests.

The GraphQL calls to GitHub are fully mocked — these tests exercise the router
wiring (repo/token gating, response shaping) and the drag-drop status update
endpoint, not the live GitHub API.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.routers import projects as projects_router


class _FakeClient:
    """Stand-in for GitHubClient capturing the last status mutation."""

    last_status_call: dict[str, Any] | None = None

    def __init__(self, *, token: str) -> None:
        self.token = token

    async def aclose(self) -> None:
        return None

    async def list_repo_projects(self, repo: str) -> list[dict[str, Any]]:
        return [
            {
                "id": "PVT_1",
                "number": 7,
                "title": "Roadmap",
                "url": "https://github.com/orgs/acme/projects/7",
                "closed": False,
                "short_description": "Team roadmap",
            }
        ]

    async def get_project_board(
        self, project_id: str, *, status_field_name: str = "Status"
    ) -> dict[str, Any]:
        return {
            "id": project_id,
            "title": "Roadmap",
            "url": "https://github.com/orgs/acme/projects/7",
            "status_field": {
                "id": "FIELD_1",
                "name": "Status",
                "options": [
                    {"id": "OPT_TODO", "name": "Todo"},
                    {"id": "OPT_DONE", "name": "Done"},
                ],
            },
            "items": [
                {
                    "id": "ITEM_1",
                    "type": "issue",
                    "number": 12,
                    "title": "Fix login",
                    "url": "https://github.com/acme/app/issues/12",
                    "state": "OPEN",
                    "status_option_id": "OPT_TODO",
                    "status_name": "Todo",
                    "labels": [{"name": "bug", "color": "d73a4a"}],
                }
            ],
        }

    async def set_project_item_status(
        self, *, project_id: str, item_id: str, field_id: str, option_id: str
    ) -> str:
        type(self).last_status_call = {
            "project_id": project_id,
            "item_id": item_id,
            "field_id": field_id,
            "option_id": option_id,
        }
        return item_id


@pytest.fixture()
def _mock_github(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _repo(_session: Any) -> str:
        return "acme/app"

    async def _enabled(_session: Any) -> bool:
        return True

    async def _token(_session: Any) -> str:
        return "tok"

    monkeypatch.setattr(projects_router, "GitHubClient", _FakeClient)
    monkeypatch.setattr(projects_router, "resolve_global_github_repo", _repo)
    monkeypatch.setattr(projects_router, "resolve_issue_associations_enabled", _enabled)
    monkeypatch.setattr(projects_router, "resolve_github_token", _token)
    _FakeClient.last_status_call = None


def test_list_projects(_mock_github: None) -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/github/projects")
        assert r.status_code == 200
        body = r.json()
        assert body[0]["number"] == 7
        assert body[0]["title"] == "Roadmap"


def test_get_board_columns_and_cards(_mock_github: None) -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/github/projects/PVT_1/board")
        assert r.status_code == 200
        board = r.json()
        assert board["status_field"]["options"][0]["name"] == "Todo"
        assert board["items"][0]["status_option_id"] == "OPT_TODO"
        assert board["items"][0]["labels"][0]["name"] == "bug"


def test_update_item_status(_mock_github: None) -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/github/projects/PVT_1/items/ITEM_1/status",
            json={"field_id": "FIELD_1", "option_id": "OPT_DONE"},
        )
        assert r.status_code == 200
        assert r.json() == {"item_id": "ITEM_1", "option_id": "OPT_DONE"}
        assert _FakeClient.last_status_call == {
            "project_id": "PVT_1",
            "item_id": "ITEM_1",
            "field_id": "FIELD_1",
            "option_id": "OPT_DONE",
        }


def test_projects_gated_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _disabled(_session: Any) -> bool:
        return False

    monkeypatch.setattr(projects_router, "resolve_issue_associations_enabled", _disabled)
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/github/projects")
        assert r.status_code == 403
