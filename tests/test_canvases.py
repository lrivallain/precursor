"""Conversation canvases: the service, the MCP tools, the API and the built-in skill."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal, init_db
from precursor.backend.main import create_app
from precursor.backend.models import Chat
from precursor.backend.services import artifacts, canvases, skills
from precursor.backend.services.canvases import CanvasError
from precursor.backend.services.mcp import precursor_server as ps
from precursor.backend.services.mcp.workspace_links import link_from_result
from precursor.backend.services.workspaces import allocate_slug
from tests.support import set_app_setting


async def _set_expose(value: dict[str, bool]) -> None:
    await init_db()
    await set_app_setting("mcp_expose", value)


@pytest.fixture(autouse=True)
async def _reset_expose():
    yield
    await _set_expose({})


async def _chat() -> Chat:
    await init_db()
    async with SessionLocal() as session:
        chat = Chat(title="Canvas chat", slug=f"canvas-chat-{uuid.uuid4().hex[:8]}")
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        return chat


class _Result:
    def __init__(self, structured: dict) -> None:
        self.structured_content = structured


# --------------------------------------------------------------------------
# service
# --------------------------------------------------------------------------
def test_names_kinds_and_paths() -> None:
    assert canvases.canvas_name("Cycle de vie TCP") == "cycle-de-vie-tcp"
    assert canvases.canvas_name("flow.canvas.html") == "flow"
    with pytest.raises(CanvasError):
        canvases.canvas_name("!!!")
    assert canvases.kind_for("a/b.canvas.html") == "html"
    assert canvases.kind_for("a/b.mmd") == "mermaid"
    assert canvases.kind_for("a/b.html") is None
    with pytest.raises(CanvasError):
        canvases.check_conversation_id("../etc")


def test_write_replace_list_and_kind_change() -> None:
    conv = str(uuid.uuid4())
    first = canvases.write_canvas(conv, "Flow", "mermaid", "graph TD; A-->B")
    assert first.path == f"{conv}/flow.mmd"
    # Same name, another kind: the old file goes.
    second = canvases.write_canvas(conv, "flow", "svg", "<svg viewBox='0 0 1 1'></svg>")
    assert [c.path for c in canvases.list_canvases(conv)] == [second.path]
    assert not (canvases.canvas_root() / first.path).exists()
    canvases.remove_conversation_canvases(conv)
    assert canvases.list_canvases(conv) == []


def test_apply_edits_requires_unique_matches() -> None:
    assert canvases.apply_edits("a b c", [{"find": "b", "replace": "B"}]) == "a B c"
    with pytest.raises(CanvasError, match="not found"):
        canvases.apply_edits("a", [{"find": "z", "replace": ""}])
    with pytest.raises(CanvasError, match="2 times"):
        canvases.apply_edits("a a", [{"find": "a", "replace": ""}])


def test_state_round_trip_and_limit() -> None:
    conv = str(uuid.uuid4())
    assert canvases.read_state(conv, "sim") is None
    canvases.write_state(conv, "sim", {"speed": 3})
    assert canvases.read_state(conv, "sim") == {"speed": 3}
    with pytest.raises(CanvasError):
        canvases.write_state(conv, "sim", "x" * (canvases.MAX_STATE_BYTES + 1))
    # State files never show up as canvases.
    assert canvases.list_canvases(conv) == []


async def test_canvas_slug_is_reserved() -> None:
    await init_db()
    async with SessionLocal() as session:
        assert await allocate_slug(session, "canvas") == "canvas-2"


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------
async def test_tools_write_edit_read_and_attach_link() -> None:
    await _set_expose({"canvas": True})
    chat = await _chat()
    written = await ps.write_canvas(
        conversation_id=chat.public_id, name="Slow start", kind="html", content="<p>cwnd=1</p>"
    )
    assert "error" not in written, written
    assert written["created"] is True
    assert written["embed"] == f"```canvas\n{chat.public_id}/slow-start.canvas.html\n```"
    # The turn engine reads this link to attach the canvas as an artifact.
    assert link_from_result(_Result(written)) == {
        "slug": "canvas",
        "path": f"{chat.public_id}/slow-start.canvas.html",
        "written": True,
    }

    edited = await ps.edit_canvas(
        conversation_id=chat.public_id,
        name="slow-start",
        edits=[{"find": "cwnd=1", "replace": "cwnd=2"}],
    )
    assert edited["created"] is False
    read = await ps.read_canvas(conversation_id=chat.public_id, name="slow-start")
    assert read["content"] == "<p>cwnd=2</p>"
    listing = await ps.list_canvases(conversation_id=chat.public_id)
    assert [c["name"] for c in listing["canvases"]] == ["slow-start"]


async def test_tools_refuse_unknown_conversation_and_bad_kind() -> None:
    await _set_expose({"canvas": True})
    missing = await ps.write_canvas(
        conversation_id=str(uuid.uuid4()), name="x", kind="svg", content="<svg/>"
    )
    assert "No conversation" in missing["error"]
    chat = await _chat()
    bad = await ps.write_canvas(
        conversation_id=chat.public_id,
        name="x",
        kind="pdf",
        content="",  # type: ignore[arg-type]
    )
    assert "kind must be" in bad["error"]


async def test_canvas_section_is_on_by_default_and_can_be_turned_off() -> None:
    await _set_expose({})
    guide = await ps.canvas_guide()
    assert "window.precursor" in guide["guide"]
    await _set_expose({"canvas": False})
    assert "not exposed" in (await ps.canvas_guide())["error"]


async def test_prompt_names_the_conversation_and_its_canvases() -> None:
    await _set_expose({})
    chat = await _chat()
    canvases.write_canvas(chat.public_id, "arch", "drawio", "<mxfile/>")
    async with SessionLocal() as session:
        prompt = await artifacts.artifacts_prompt(session, "chat", chat.id)
    assert chat.public_id in prompt
    assert "canvas_guide" in prompt
    assert f"arch (drawio, embed path: {chat.public_id}/arch.drawio)" in prompt
    await _set_expose({"canvas": False})
    async with SessionLocal() as session:
        assert "canvas_guide" not in await artifacts.artifacts_prompt(session, "chat", chat.id)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
async def test_api_lists_canvases_and_keeps_state() -> None:
    chat = await _chat()
    canvases.write_canvas(chat.public_id, "sim", "html", "<p>x</p>")
    with TestClient(create_app()) as client:
        ws = client.get("/api/canvas/workspace").json()
        assert ws["slug"] == "canvas"
        listed = client.get(f"/api/canvas/{chat.public_id}").json()
        assert [c["kind"] for c in listed] == ["html"]
        assert client.get(f"/api/canvas/{chat.public_id}/sim/state").json() == {"state": None}
        saved = client.put(f"/api/canvas/{chat.public_id}/sim/state", json={"state": [1, 2]})
        assert saved.status_code == 200
        assert client.get(f"/api/canvas/{chat.public_id}/sim/state").json() == {"state": [1, 2]}
        assert client.put(f"/api/canvas/{chat.public_id}/nope/state", json={}).status_code == 404
        assert client.get("/api/canvas/not-a-uuid").status_code == 400
        raw = client.get(f"/raw/canvas/{chat.public_id}/sim.canvas.html")
        assert raw.status_code == 200 and "text/html" in raw.headers["content-type"]

        assert client.delete(f"/api/chats/{chat.id}").status_code == 204
    assert not (canvases.canvas_root() / chat.public_id).exists()


# --------------------------------------------------------------------------
# built-in skill
# --------------------------------------------------------------------------
async def test_builtin_canvas_skill_toggles_but_cannot_be_edited() -> None:
    await init_db()
    async with SessionLocal() as session:
        skill = await skills.get_resolved(session, "canvas")
        assert skill is not None and skill.builtin and skill.active
        with pytest.raises(skills.SkillError):
            await skills.update_skill(session, "canvas", instructions="x")
        with pytest.raises(skills.SkillError):
            await skills.delete_skill(session, "canvas")
        off = await skills.update_skill(session, "canvas", enabled=False)
        assert off.active is False
        on = await skills.update_skill(session, "canvas", enabled=True)
        assert on.active is True
    assert Path(skills.BUILTIN_SKILLS_DIR / "canvas" / "SKILL.md").is_file()


def test_canvas_workspace_is_flagged_and_listed_after_definitions() -> None:
    with TestClient(create_app()) as client:
        client.get("/api/canvas/workspace")
        assert (
            client.post("/api/workspaces", json={"name": "Later", "kind": "local"}).status_code
            == 201
        )
        listed = client.get("/api/workspaces").json()
    flags = [(w["hosts_definitions"], w["hosts_canvases"]) for w in listed]
    canvas_at = next(i for i, (_, canvas) in enumerate(flags) if canvas)
    assert listed[canvas_at]["slug"] == "canvas"
    # Only the definitions workspace may come before it.
    assert all(defs for defs, _ in flags[:canvas_at])
