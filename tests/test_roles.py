"""Tests for the Assistant Roles feature — CRUD, default protection, injection."""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.main import create_app


def test_default_role_seeded_and_protected() -> None:
    app = create_app()
    with TestClient(app) as client:
        roles = client.get("/api/roles").json()
        defaults = [r for r in roles if r["is_default"]]
        assert len(defaults) == 1
        default = defaults[0]
        assert default["name"] == "default"
        assert default["system_prompt"] == ""

        # Default cannot be deleted or renamed.
        assert client.delete(f"/api/roles/{default['id']}").status_code == 400
        rename = client.patch(f"/api/roles/{default['id']}", json={"name": "boss"})
        assert rename.status_code == 400


def test_role_crud_lifecycle() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/roles",
            json={"name": "reviewer", "system_prompt": "Be a strict code reviewer."},
        )
        assert r.status_code == 201
        role = r.json()
        assert role["name"] == "reviewer"
        assert role["is_default"] is False
        rid = role["id"]

        # Reserved name + duplicate (case-insensitive) are rejected.
        assert client.post("/api/roles", json={"name": "default"}).status_code == 400
        assert client.post("/api/roles", json={"name": "Reviewer"}).status_code == 409

        # Edit the prompt + rename.
        r = client.patch(
            f"/api/roles/{rid}",
            json={"name": "auditor", "system_prompt": "Audit carefully."},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "auditor"
        assert r.json()["system_prompt"] == "Audit carefully."

        assert client.delete(f"/api/roles/{rid}").status_code == 204
        assert client.get(f"/api/roles/{rid}").status_code == 404


def test_assign_role_to_topic_and_revert_on_delete() -> None:
    app = create_app()
    with TestClient(app) as client:
        role = client.post(
            "/api/roles", json={"name": "architect", "system_prompt": "Ask questions first."}
        ).json()
        topic = client.post("/api/topics", json={"title": "Design"}).json()

        # Assign the role.
        r = client.patch(f"/api/topics/{topic['id']}", json={"role_id": role["id"]})
        assert r.status_code == 200
        assert r.json()["role_id"] == role["id"]

        # Deleting the role reverts the topic to default (role_id cleared).
        assert client.delete(f"/api/roles/{role['id']}").status_code == 204
        refreshed = client.get(f"/api/topics/{topic['id']}").json()
        assert refreshed["role_id"] is None


def test_assign_role_to_chat_and_workspace() -> None:
    app = create_app()
    with TestClient(app) as client:
        role = client.post(
            "/api/roles", json={"name": "editor", "system_prompt": "Fix grammar."}
        ).json()

        chat = client.post("/api/chats", json={"title": "Notes"}).json()
        r = client.patch(f"/api/chats/{chat['id']}", json={"role_id": role["id"]})
        assert r.status_code == 200
        assert r.json()["role_id"] == role["id"]

        ws = client.post("/api/workspaces", json={"name": "Docs", "kind": "local"}).json()
        r = client.patch(f"/api/workspaces/{ws['id']}", json={"role_id": role["id"]})
        assert r.status_code == 200
        assert r.json()["role_id"] == role["id"]


def test_selecting_default_clears_role_id() -> None:
    """PATCHing role_id=null (the default selection) must clear an assigned role."""
    app = create_app()
    with TestClient(app) as client:
        role = client.post(
            "/api/roles", json={"name": "coach", "system_prompt": "Be encouraging."}
        ).json()
        chat = client.post("/api/chats", json={"title": "Standup"}).json()

        client.patch(f"/api/chats/{chat['id']}", json={"role_id": role["id"]})
        cleared = client.patch(f"/api/chats/{chat['id']}", json={"role_id": None})
        assert cleared.status_code == 200
        assert cleared.json()["role_id"] is None


def test_role_prompt_injected_into_topic_system_context() -> None:
    """The assigned role's prompt must reach the LLM system message."""
    import anyio

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Topic
    from precursor.backend.services.turn_engine import build_system_context

    app = create_app()
    with TestClient(app) as client:
        role = client.post(
            "/api/roles",
            json={"name": "pirate", "system_prompt": "Always answer like a pirate."},
        ).json()
        topic = client.post("/api/topics", json={"title": "Yarr"}).json()
        client.patch(f"/api/topics/{topic['id']}", json={"role_id": role["id"]})

        async def _check() -> str:
            async with SessionLocal() as session:
                t = await session.get(Topic, topic["id"])
                assert t is not None
                return await build_system_context(session, t)

        prompt = anyio.run(_check)
        assert "Always answer like a pirate." in prompt


def test_assign_role_to_live_session_and_revert_on_delete() -> None:
    """A Live meeting session carries a role that reverts to default on delete."""
    app = create_app()
    with TestClient(app) as client:
        role = client.post(
            "/api/roles",
            json={"name": "facilitator", "system_prompt": "Keep the meeting on track."},
        ).json()
        sid = client.post("/api/live", json={"title": "Standup"}).json()["id"]

        r = client.patch(f"/api/live/{sid}", json={"role_id": role["id"]})
        assert r.status_code == 200
        assert r.json()["role_id"] == role["id"]

        # Selecting the default (null) clears it.
        cleared = client.patch(f"/api/live/{sid}", json={"role_id": None})
        assert cleared.json()["role_id"] is None

        # Reassign, then delete the role — the session reverts to default.
        client.patch(f"/api/live/{sid}", json={"role_id": role["id"]})
        assert client.delete(f"/api/roles/{role['id']}").status_code == 204
        assert client.get(f"/api/live/{sid}").json()["role_id"] is None


def test_assign_role_to_agent_and_revert_on_delete() -> None:
    """An agent session carries a role that reverts to default on delete."""
    import anyio

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    app = create_app()
    with TestClient(app) as client:
        role = client.post(
            "/api/roles",
            json={"name": "researcher", "system_prompt": "Dig deep before answering."},
        ).json()

        async def _make() -> int:
            async with SessionLocal() as session:
                agent = AgentSession(title="Task", task_prompt="seed", status="idle")
                session.add(agent)
                await session.commit()
                await session.refresh(agent)
                return agent.id

        agent_id = anyio.run(_make)

        r = client.patch(f"/api/agents/{agent_id}", json={"role_id": role["id"]})
        assert r.status_code == 200
        assert r.json()["role_id"] == role["id"]

        assert client.delete(f"/api/roles/{role['id']}").status_code == 204
        assert client.get(f"/api/agents/{agent_id}").json()["role_id"] is None


def test_role_persona_grounds_live_chat() -> None:
    """The Live session's role prompt is injected into the Ask-assistant chat grounding."""
    import anyio

    from precursor.backend.db import SessionLocal
    from precursor.backend.services.meeting_analysis import live_chat_grounding

    app = create_app()
    with TestClient(app) as client:
        role = client.post(
            "/api/roles",
            json={"name": "sme", "system_prompt": "Answer as a domain expert."},
        ).json()
        sid = client.post("/api/live", json={"title": "Review"}).json()["id"]
        client.patch(f"/api/live/{sid}", json={"role_id": role["id"]})
        # Spawn the assistant chat so grounding can resolve the session by chat_id.
        chat = client.post(f"/api/live/{sid}/chat").json()

        async def _ground() -> str:
            async with SessionLocal() as session:
                return await live_chat_grounding(session, chat["id"])

        grounding = anyio.run(_ground)
        assert "Answer as a domain expert." in grounding
