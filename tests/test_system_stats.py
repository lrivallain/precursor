"""Tests for system-footprint statistics — DB, blobs, issues, entity counts."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import delete

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AgentSession, Chat, Topic, Workspace
from precursor.backend.services import system_stats as system_stats_module
from precursor.backend.services.system_stats import compute_system_stats


def _init_db() -> None:
    # Lifespan runs init_db (alembic upgrade head) before we touch the tables.
    with TestClient(create_app()):
        pass


async def _reset_entities() -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AgentSession))
        await session.execute(delete(Workspace))
        await session.execute(delete(Chat))
        await session.execute(delete(Topic))
        await session.commit()


async def test_compute_system_stats_counts_and_sizes() -> None:
    _init_db()
    await _reset_entities()

    async with SessionLocal() as session:
        session.add_all(
            [
                Topic(title="One", slug="sys-stats-one"),
                Topic(title="Two", slug="sys-stats-two"),
                Chat(title="Chat", slug="sys-stats-chat"),
                Workspace(name="WS", slug="sys-stats-ws", repo_url="https://example/repo.git"),
            ]
        )
        await session.commit()

    async with SessionLocal() as session:
        stats = await compute_system_stats(session)

    assert stats.entities.topics == 2
    assert stats.entities.chats == 1
    assert stats.entities.agents == 0
    assert stats.entities.workspaces == 1

    # SQLite file-backed DB reports a positive on-disk size and a per-table list.
    assert stats.database.engine == "sqlite"
    assert stats.database.size_bytes and stats.database.size_bytes > 0
    table_names = {t.name for t in stats.database.tables}
    assert {"topics", "chats", "workspaces", "agent_sessions"} <= table_names
    topics_row = next(t for t in stats.database.tables if t.name == "topics")
    assert topics_row.row_count == 2

    # Blob store dir may be empty but the stats are still well-formed.
    assert stats.blobs.count >= 0
    assert stats.blobs.size_bytes >= 0


async def test_issue_stats_unconfigured_when_no_repo_or_token(monkeypatch) -> None:
    _init_db()

    async def _no_repo(_session) -> str:
        return ""

    async def _no_token(_session) -> str:
        return ""

    monkeypatch.setattr(system_stats_module, "resolve_global_github_repo", _no_repo)
    monkeypatch.setattr(system_stats_module, "resolve_github_token", _no_token)

    async with SessionLocal() as session:
        stats = await compute_system_stats(session)

    assert stats.issues.configured is False
    assert stats.issues.open is None
    assert stats.issues.closed is None


async def test_issue_stats_counts_open_and_closed(monkeypatch) -> None:
    _init_db()

    async def _repo(_session) -> str:
        return "owner/name"

    async def _token(_session) -> str:
        return "tok"

    monkeypatch.setattr(system_stats_module, "resolve_global_github_repo", _repo)
    monkeypatch.setattr(system_stats_module, "resolve_github_token", _token)

    class _FakeClient:
        def __init__(self, *, token: str) -> None:
            pass

        async def count_issues(self, repo: str, *, state: str) -> int:
            return 7 if state == "open" else 3

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(system_stats_module, "GitHubClient", _FakeClient)

    async with SessionLocal() as session:
        stats = await compute_system_stats(session)

    assert stats.issues.configured is True
    assert stats.issues.repo == "owner/name"
    assert stats.issues.open == 7
    assert stats.issues.closed == 3
    assert stats.issues.error is None


def test_system_stats_endpoint() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/stats/system")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"database", "blobs", "issues", "entities"}
        assert set(body["entities"]) == {"topics", "chats", "agents", "workspaces"}
        assert isinstance(body["database"]["tables"], list)
