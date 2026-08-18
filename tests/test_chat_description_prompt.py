"""Tests for issue #32 — chat description as discussion context or system prompt.

Covers the three states (off / context / system prompt), persistence of the
``description_as_system_prompt`` flag, and the deterministic interaction with an
assigned Role.
"""

from __future__ import annotations

import anyio
from fastapi.testclient import TestClient

from precursor.backend.main import create_app


def _build_context(chat_id: int) -> str:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Chat
    from precursor.backend.services.turn_engine import build_chat_system_context

    async def _check() -> str:
        async with SessionLocal() as session:
            chat = await session.get(Chat, chat_id)
            assert chat is not None
            return await build_chat_system_context(session, chat)

    return anyio.run(_check)


def _apply_prompt(chat_id: int, user_contents: list[str]) -> list[str]:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Chat
    from precursor.backend.services.llm.base import ChatMessage
    from precursor.backend.services.turn_engine import apply_chat_system_prompt

    async def _check() -> list[str]:
        async with SessionLocal() as session:
            chat = await session.get(Chat, chat_id)
            assert chat is not None
            history = [ChatMessage(role="user", content=c) for c in user_contents]
            out = apply_chat_system_prompt(chat, history)
            return [m.content for m in out]

    return anyio.run(_check)


def test_empty_description_adds_nothing() -> None:
    app = create_app()
    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"title": "Plain"}).json()
        ctx = _build_context(chat["id"])
        assert "Chat description:" not in ctx
        # System-prompt application is a no-op without a description.
        assert _apply_prompt(chat["id"], ["hi"]) == ["hi"]


def test_description_injected_as_context_by_default() -> None:
    app = create_app()
    with TestClient(app) as client:
        chat = client.post(
            "/api/chats",
            json={"title": "Trip", "description": "Budget 3000, vegetarian"},
        ).json()
        assert chat["description_as_system_prompt"] is False
        ctx = _build_context(chat["id"])
        assert "Chat description: Budget 3000, vegetarian" in ctx
        # In context mode the per-message enforcement is a no-op.
        assert _apply_prompt(chat["id"], ["plan dinner"]) == ["plan dinner"]


def test_description_enforced_as_system_prompt_each_turn() -> None:
    app = create_app()
    with TestClient(app) as client:
        chat = client.post(
            "/api/chats",
            json={
                "title": "JSON",
                "description": "Respond only with valid JSON",
                "description_as_system_prompt": True,
            },
        ).json()
        assert chat["description_as_system_prompt"] is True

        # Not duplicated as soft context when in system-prompt mode.
        ctx = _build_context(chat["id"])
        assert "Chat description:" not in ctx

        # Prepended to every user turn.
        out = _apply_prompt(chat["id"], ["first", "second"])
        assert all("Respond only with valid JSON" in c for c in out)
        assert out[0].endswith("first")
        assert out[1].endswith("second")


def test_checkbox_on_empty_description_is_noop() -> None:
    app = create_app()
    with TestClient(app) as client:
        chat = client.post(
            "/api/chats",
            json={"title": "Empty", "description_as_system_prompt": True},
        ).json()
        assert _apply_prompt(chat["id"], ["hi"]) == ["hi"]


def test_flag_persists_across_reload() -> None:
    app = create_app()
    with TestClient(app) as client:
        chat = client.post(
            "/api/chats",
            json={"title": "Persist", "description": "always French"},
        ).json()
        client.patch(
            f"/api/chats/{chat['id']}",
            json={"description_as_system_prompt": True},
        )
        reloaded = client.get(f"/api/chats/{chat['id']}").json()
        assert reloaded["description_as_system_prompt"] is True

        # Toggle back off and confirm it sticks.
        client.patch(
            f"/api/chats/{chat['id']}",
            json={"description_as_system_prompt": False},
        )
        reloaded = client.get(f"/api/chats/{chat['id']}").json()
        assert reloaded["description_as_system_prompt"] is False


def test_role_and_system_prompt_description_are_deterministic() -> None:
    app = create_app()
    with TestClient(app) as client:
        role = client.post(
            "/api/roles",
            json={"name": "french-pirate", "system_prompt": "Always answer like a pirate."},
        ).json()
        chat = client.post(
            "/api/chats",
            json={
                "title": "Mix",
                "description": "Respond only in French",
                "description_as_system_prompt": True,
            },
        ).json()
        client.patch(f"/api/chats/{chat['id']}", json={"role_id": role["id"]})

        # Role persona lives in the system context; the description is enforced
        # per user turn — a stable, non-overlapping split.
        ctx = _build_context(chat["id"])
        assert "Always answer like a pirate." in ctx
        assert "Respond only in French" not in ctx

        out = _apply_prompt(chat["id"], ["bonjour"])
        assert "Respond only in French" in out[0]


def _write_skill(name: str, body: str) -> None:
    """Author an enabled skill on disk the way another tool would."""
    from pathlib import Path

    from precursor.backend.config import get_settings

    path = Path(get_settings().skills_dir) / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\n---\n\n{body}\n", encoding="utf-8")


def _apply_prompt_expanded(chat_id: int, user_contents: list[str]) -> list[str]:
    """Mirror the router: expand skill refs in the description, then apply it."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Chat
    from precursor.backend.services import skills as skills_service
    from precursor.backend.services.llm.base import ChatMessage
    from precursor.backend.services.turn_engine import apply_chat_system_prompt

    async def _check() -> list[str]:
        async with SessionLocal() as session:
            chat = await session.get(Chat, chat_id)
            assert chat is not None
            description = await skills_service.expand_references(session, chat.description or "")
            history = [ChatMessage(role="user", content=c) for c in user_contents]
            out = apply_chat_system_prompt(chat, history, description=description)
            return [m.content for m in out]

    return anyio.run(_check)


def test_description_can_trigger_a_skill_as_system_prompt() -> None:
    """A `/skill-name` in the description expands into the enforced instruction."""
    _write_skill("formalise", "Rewrite the user's text in formal French.")
    app = create_app()
    with TestClient(app) as client:
        client.get("/api/skills")  # discovery
        client.patch("/api/skills/formalise", json={"enabled": True})
        chat = client.post(
            "/api/chats",
            json={
                "title": "Rewriter",
                "description": "For every message: /formalise",
                "description_as_system_prompt": True,
            },
        ).json()

        out = _apply_prompt_expanded(chat["id"], ["salut ca va"])
        assert "Rewrite the user's text in formal French." in out[0]
        assert "/formalise" not in out[0]
        assert out[0].endswith("salut ca va")


def test_description_skill_reference_expands_in_context_mode() -> None:
    _write_skill("ctx-skill", "CTX BODY")
    app = create_app()
    with TestClient(app) as client:
        client.get("/api/skills")
        client.patch("/api/skills/ctx-skill", json={"enabled": True})
        chat = client.post(
            "/api/chats",
            json={"title": "Ctx", "description": "Follow /ctx-skill closely"},
        ).json()
        ctx = _build_context(chat["id"])
        assert "Chat description: Follow CTX BODY closely" in ctx


def test_disabled_skill_reference_stays_literal() -> None:
    """An inactive skill must not leak its body into the system prompt."""
    _write_skill("off-skill", "SHOULD NOT APPEAR")
    app = create_app()
    with TestClient(app) as client:
        client.get("/api/skills")  # discovered, but left disabled
        chat = client.post(
            "/api/chats",
            json={
                "title": "Off",
                "description": "Use /off-skill",
                "description_as_system_prompt": True,
            },
        ).json()
        out = _apply_prompt_expanded(chat["id"], ["hi"])
        assert "SHOULD NOT APPEAR" not in out[0]
        assert "/off-skill" in out[0]
