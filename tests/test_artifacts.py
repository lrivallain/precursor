"""Conversation artifacts: workspace files attached to topics and chats."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from mcp.types import CallToolResult

from precursor.backend.config import get_settings
from precursor.backend.main import create_app
from precursor.backend.schemas.artifact import WorkspaceTurnContext
from precursor.backend.services import artifacts as artifacts_service
from precursor.backend.services.mcp import workspace_links as links
from precursor.backend.services.turn_engine import ToolResultTurn, persist_tool_result


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def _workspace(client: TestClient, **files: str) -> tuple[dict, Path]:
    resp = client.post(
        "/api/workspaces", json={"name": f"Art {uuid.uuid4().hex[:6]}", "kind": "local"}
    )
    assert resp.status_code == 201, resp.text
    ws = resp.json()
    root = Path(get_settings().workspaces_dir) / ws["slug"]
    for name, body in files.items():
        target = root / name.replace("__", "/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return ws, root


def _chat(client: TestClient) -> int:
    return client.post("/api/chats", json={"title": "Discuss"}).json()["id"]


def _topic(client: TestClient) -> int:
    return client.post("/api/topics", json={"title": "Discuss topic"}).json()["id"]


def _result(structured: object) -> CallToolResult:
    return CallToolResult(content=[], structured_content=structured)


def test_link_marks_a_write_but_not_a_read() -> None:
    base = {"workspace_slug": "ws", "path": "a.md"}
    assert links.link_from_result(_result(base)) == {"slug": "ws", "path": "a.md"}
    assert links.link_from_result(_result({**base, "written": True})) == {
        "slug": "ws",
        "path": "a.md",
        "written": True,
    }
    assert links.link_from_result(_result({**base, "created": True}))["written"] is True


def test_attach_list_and_detach(client: TestClient) -> None:
    ws, _ = _workspace(client, **{"a.md": "hi"})
    chat_id = _chat(client)
    topic_id = _topic(client)
    for base in (f"/api/chats/{chat_id}", f"/api/topics/{topic_id}"):
        body = {"workspace_id": ws["id"], "path": "/a.md"}
        created = client.post(f"{base}/artifacts", json=body)
        assert created.status_code == 201, created.text
        assert created.json()["path"] == "a.md"
        assert created.json()["source"] == "manual"
        # Attaching again refreshes the link instead of duplicating it.
        client.post(f"{base}/artifacts", json=body)
        listed = client.get(f"{base}/artifacts").json()
        assert [a["path"] for a in listed] == ["a.md"]
        assert listed[0]["exists"] is True
        assert listed[0]["workspace_slug"] == ws["slug"]
        assert client.delete(f"{base}/artifacts/{listed[0]['id']}").status_code == 204
        assert client.get(f"{base}/artifacts").json() == []
        assert client.delete(f"{base}/artifacts/{listed[0]['id']}").status_code == 404


def test_attach_refuses_missing_and_escaping_paths(client: TestClient) -> None:
    ws, _ = _workspace(client)
    chat_id = _chat(client)
    url = f"/api/chats/{chat_id}/artifacts"
    assert client.post(url, json={"workspace_id": ws["id"], "path": "nope.md"}).status_code == 404
    assert client.post(url, json={"workspace_id": ws["id"], "path": "../x"}).status_code == 400
    assert client.post(url, json={"workspace_id": 99999, "path": "a.md"}).status_code == 404


def test_a_removed_file_reads_as_missing(client: TestClient) -> None:
    ws, root = _workspace(client, **{"a.md": "hi"})
    chat_id = _chat(client)
    url = f"/api/chats/{chat_id}/artifacts"
    client.post(url, json={"workspace_id": ws["id"], "path": "a.md"})
    (root / "a.md").unlink()
    assert client.get(url).json()[0]["exists"] is False


def test_an_artifact_cannot_be_detached_through_another_conversation(client: TestClient) -> None:
    ws, _ = _workspace(client, **{"a.md": "hi"})
    one, two = _chat(client), _chat(client)
    art = client.post(
        f"/api/chats/{one}/artifacts", json={"workspace_id": ws["id"], "path": "a.md"}
    ).json()
    assert client.delete(f"/api/chats/{two}/artifacts/{art['id']}").status_code == 404
    assert len(client.get(f"/api/chats/{one}/artifacts").json()) == 1


def test_deleting_a_conversation_or_workspace_drops_its_artifacts(client: TestClient) -> None:
    ws, _ = _workspace(client, **{"a.md": "hi"})
    chat_id, topic_id = _chat(client), _topic(client)
    for base in (f"/api/chats/{chat_id}", f"/api/topics/{topic_id}"):
        client.post(f"{base}/artifacts", json={"workspace_id": ws["id"], "path": "a.md"})
    assert len(client.get(f"/api/workspaces/{ws['id']}/discussions").json()) == 2

    assert client.delete(f"/api/chats/{chat_id}").status_code == 204
    assert client.delete(f"/api/topics/{topic_id}").status_code == 204
    assert client.get(f"/api/workspaces/{ws['id']}/discussions").json() == []

    other = _chat(client)
    client.post(f"/api/chats/{other}/artifacts", json={"workspace_id": ws["id"], "path": "a.md"})
    assert client.delete(f"/api/workspaces/{ws['id']}").status_code == 204
    assert client.get(f"/api/chats/{other}/artifacts").json() == []


def test_discussions_lead_with_the_open_file_and_skip_archived(client: TestClient) -> None:
    ws, _ = _workspace(client, **{"a.md": "a", "b.md": "b"})
    on_a, on_b, archived = _chat(client), _chat(client), _chat(client)
    for chat_id, path in ((on_a, "a.md"), (on_b, "b.md"), (archived, "a.md")):
        client.post(
            f"/api/chats/{chat_id}/artifacts", json={"workspace_id": ws["id"], "path": path}
        )
    topic_id = _topic(client)
    client.post(
        f"/api/topics/{topic_id}/artifacts", json={"workspace_id": ws["id"], "path": "b.md"}
    )
    client.post(f"/api/chats/{archived}/archive")

    items = client.get(f"/api/workspaces/{ws['id']}/discussions", params={"path": "b.md"}).json()
    ids = [(i["kind"], i["id"]) for i in items]
    assert ("chat", archived) not in ids
    assert {ids[0], ids[1]} == {("chat", on_b), ("topic", topic_id)}
    assert [i["has_file"] for i in items] == [True, True, False]
    assert items[-1]["id"] == on_a
    topic_item = next(i for i in items if i["kind"] == "topic")
    assert topic_item["public_id"]
    assert client.get("/api/workspaces/99999/discussions").status_code == 404


def test_a_written_file_is_recorded_once_and_a_read_is_not(client: TestClient) -> None:
    ws, _ = _workspace(client, **{"a.md": "hi"})
    chat_id = _chat(client)
    call = SimpleNamespace(id="c1", name="write_workspace_file", arguments={})

    async def run() -> None:
        read = ToolResultTurn(call, "ok", False, {"slug": ws["slug"], "path": "a.md"})
        await persist_tool_result("chat", chat_id, read)
        for _ in range(2):
            wrote = ToolResultTurn(
                call, "ok", False, {"slug": ws["slug"], "path": "a.md", "written": True}
            )
            await persist_tool_result("chat", chat_id, wrote)

    url = f"/api/chats/{chat_id}/artifacts"
    client.portal.call(run)
    listed = client.get(url).json()
    assert [(a["path"], a["source"]) for a in listed] == [("a.md", "assistant")]


def test_a_read_alone_attaches_nothing(client: TestClient) -> None:
    ws, _ = _workspace(client, **{"a.md": "hi"})
    chat_id = _chat(client)
    call = SimpleNamespace(id="c1", name="read_workspace_file", arguments={})

    async def run() -> None:
        read = ToolResultTurn(call, "ok", False, {"slug": ws["slug"], "path": "a.md"})
        await persist_tool_result("chat", chat_id, read)

    client.portal.call(run)
    assert client.get(f"/api/chats/{chat_id}/artifacts").json() == []


def test_turn_context_inlines_the_open_file_and_system_prompt_lists_artifacts(
    client: TestClient,
) -> None:
    ws, _ = _workspace(client, **{"a.md": "# Title\nbody", "docs__b.md": "other"})
    chat_id = _chat(client)
    client.post(
        f"/api/chats/{chat_id}/artifacts", json={"workspace_id": ws["id"], "path": "docs/b.md"}
    )

    async def run() -> tuple[str, str, str]:
        from precursor.backend.db import SessionLocal
        from precursor.backend.models import Chat
        from precursor.backend.services.turn_engine import build_chat_system_context

        async with SessionLocal() as session:
            ctx = await artifacts_service.workspace_turn_context(
                session, WorkspaceTurnContext(workspace_id=ws["id"], path="a.md")
            )
            bare = await artifacts_service.workspace_turn_context(
                session, WorkspaceTurnContext(workspace_id=ws["id"])
            )
            chat = await session.get(Chat, chat_id)
            assert chat is not None
            return ctx, bare, await build_chat_system_context(session, chat)

    ctx, bare, system = client.portal.call(run)
    assert f"workspace_id: {ws['id']}" in ctx
    assert "currently viewing the file: a.md" in ctx
    assert "# Title\nbody" in ctx
    assert "viewing the file" not in bare
    assert f"docs/b.md (workspace_id: {ws['id']}" in system
    assert "other" not in system
