"""GitHub Projects v2 board API tests (kanban plugin).

The GraphQL calls to GitHub are fully mocked — these exercise the plugin's
router wiring (repo/token gating via the host's shared guards, response
shaping) and the drag-drop status update endpoint, not the live GitHub API.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services import github_context
from precursor.plugin_api import (
    GitHubInsufficientScopeError,
    GitHubRepoNotAccessibleError,
)
from precursor_kanban import SECTION_ID
from precursor_kanban import router as router_module
from precursor_kanban.client import ProjectsClient


class _FakeClient:
    """Stand-in for ProjectsClient capturing the last status mutation."""

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

    monkeypatch.setattr(router_module, "ProjectsClient", _FakeClient)
    # The board resolves the repo and the feature switch itself now (it needs
    # the repo to be *optional*, which the host's combined guard can't express),
    # so patch its own bindings. The token still goes through the host guard,
    # which looks its resolver up on `github_context` at call time.
    monkeypatch.setattr(router_module, "resolve_global_github_repo", _repo)
    monkeypatch.setattr(router_module, "resolve_issue_associations_enabled", _enabled)
    monkeypatch.setattr(github_context, "resolve_github_token", _token)
    _FakeClient.last_status_call = None


def test_plugin_registers_its_section() -> None:
    """The board is only reachable because the plugin contributed a section."""
    app = create_app()
    with TestClient(app) as client:
        sections = [e for e in client.get("/api/plugins").json() if e["kind"] == "section"]
    kanban = next(e for e in sections if e["id"] == "kanban")
    assert kanban["slot"] == "app.section"
    # The id doubles as the section's top-level route, so the board lives at
    # /kanban and the SPA finds its React half under the same key.
    assert kanban["id"] == SECTION_ID


def test_list_projects(_mock_github: None) -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/github/projects")
        assert r.status_code == 200
        body = r.json()["projects"]
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

    # The board checks the feature switch itself, so patch the binding it uses.
    monkeypatch.setattr(router_module, "resolve_issue_associations_enabled", _disabled)
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/github/projects")
        assert r.status_code == 403


def test_list_projects_inaccessible_repo_returns_404(
    _mock_github: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _NotFoundClient(_FakeClient):
        async def list_repo_projects(self, repo: str) -> list[dict[str, Any]]:
            raise GitHubRepoNotAccessibleError(repo)

    monkeypatch.setattr(router_module, "ProjectsClient", _NotFoundClient)
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/github/projects")
        assert r.status_code == 404
        assert "not found or not accessible" in r.json()["detail"]


def test_list_projects_missing_scope_returns_403(
    _mock_github: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _NoScopeClient(_FakeClient):
        async def list_repo_projects(self, repo: str) -> list[dict[str, Any]]:
            raise GitHubInsufficientScopeError(["read:project"])

    monkeypatch.setattr(router_module, "ProjectsClient", _NoScopeClient)
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/github/projects")
        assert r.status_code == 403
        assert "read:project" in r.json()["detail"]


def test_board_query_captures_full_item_shape() -> None:
    """Regression: get_project_board's GraphQL query must be brace-balanced.

    A missing closing brace made GitHub reject the query with a syntax error,
    which the client surfaced as a misleading "project not found". Capture the
    query the method sends and assert its braces balance so the shape can't
    silently break again.
    """
    client = ProjectsClient(token="tok")
    captured: dict[str, str] = {}

    class _Resp:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "data": {
                    "node": {
                        "id": "PVT_1",
                        "title": "Board",
                        "url": None,
                        "field": {"id": "F", "name": "Status", "options": []},
                        "items": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [],
                        },
                    }
                }
            }

    async def _fake_post(_path: str, *, json: dict[str, Any], **_kw: Any) -> _Resp:
        captured["query"] = json["query"]
        return _Resp()

    async def _run() -> None:
        client._client.post = _fake_post  # type: ignore[method-assign]
        try:
            await client.get_project_board("PVT_1")
        finally:
            await client.aclose()

    import asyncio

    asyncio.run(_run())
    q = captured["query"]
    assert q.count("{") == q.count("}"), "unbalanced braces in board query"


def test_draft_issues_are_skipped() -> None:
    """Draft items carry no repo content, so they never become cards."""
    assert (
        ProjectsClient._project_item({"id": "X", "content": {"__typename": "DraftIssue"}}) is None
    )
