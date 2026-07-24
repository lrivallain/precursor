"""Tests for the built-in 'precursor' outbound MCP server + mcp_expose gating."""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.services.app_settings import (
    MCP_EXPOSE_SECTIONS,
    resolve_mcp_expose,
)
from precursor.backend.services.mcp import precursor_server as ps


def test_precursor_registered_as_builtin() -> None:
    app = create_app()
    with TestClient(app) as client:
        servers = client.get("/api/mcp/servers").json()
        entry = next((s for s in servers if s["name"] == "precursor"), None)
        assert entry is not None
        assert entry["builtin"] is True


def test_server_info_describes_sections() -> None:
    app = create_app()
    with TestClient(app) as client:
        info = client.get("/api/mcp/server/info").json()
        assert info["name"] == "precursor"
        assert info["transport"] == "stdio"
        assert set(info["sections"]) == set(MCP_EXPOSE_SECTIONS)
        tool_names = {t["name"] for t in info["tools"]}
        assert {"list_topics", "post_message", "create_schedule", "set_reminder"} <= tool_names


def test_mcp_expose_defaults_all_off() -> None:
    app = create_app()
    with TestClient(app) as client:
        settings = client.get("/api/settings").json()
        expose = settings["mcp_expose"]
        assert set(expose) == set(MCP_EXPOSE_SECTIONS)
        assert all(v is False for v in expose.values())


def test_mcp_expose_round_trip() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.put(
            "/api/settings",
            json={"mcp_expose": {"topics": True, "post_message": True}},
        )
        assert r.status_code == 200
        expose = r.json()["mcp_expose"]
        assert expose["topics"] is True
        assert expose["post_message"] is True
        assert expose["schedules"] is False


async def _set_expose(value_json: str) -> None:
    """Upsert the mcp_expose AppSetting row directly (tests share one DB)."""
    from precursor.backend.models import AppSetting

    async with SessionLocal() as session:
        row = await session.get(AppSetting, "mcp_expose")
        if row is None:
            session.add(AppSetting(key="mcp_expose", value=value_json))
        else:
            row.value = value_json
        await session.commit()


async def test_tool_gated_when_section_off() -> None:
    # Explicit all-off so the test is independent of other tests' writes.
    await _set_expose("{}")
    result = await ps.list_topics()
    assert "error" in result
    assert "not exposed" in result["error"]


async def test_tool_runs_when_section_enabled() -> None:
    await _set_expose('{"topics": true}')
    async with SessionLocal() as session:
        expose = await resolve_mcp_expose(session)
        assert expose["topics"] is True

    result = await ps.list_topics()
    assert "error" not in result
    assert "topics" in result


async def _make_topic(title: str) -> int:
    """Create a topic directly and return its id (tests share one DB)."""
    from precursor.backend.models import Topic
    from precursor.backend.services.slugs import allocate_unique_slug, slugify

    async with SessionLocal() as session:
        topic = Topic(
            title=title,
            slug=await allocate_unique_slug(session, slugify(title) or "topic", Topic),
        )
        session.add(topic)
        await session.commit()
        return topic.id


async def test_reminder_tools_gated_when_section_off() -> None:
    await _set_expose("{}")
    for result in (
        await ps.list_reminders(),
        await ps.get_reminder(1),
        await ps.set_reminder(1, "2026-07-20T09:00:00Z"),
        await ps.cancel_reminder(1),
    ):
        assert "error" in result
        assert "not exposed" in result["error"]


async def test_reminder_tool_lifecycle() -> None:
    await _set_expose('{"reminders": true}')
    topic_id = await _make_topic("Remind me MCP")

    # No reminder yet.
    assert "error" in await ps.get_reminder(topic_id)

    # Set one in the future so it stays "scheduled".
    created = await ps.set_reminder(topic_id, "2999-01-01T09:00:00Z", note="water the plants")
    assert created["topic_id"] == topic_id
    assert created["status"] == "scheduled"
    assert created["note"] == "water the plants"

    fetched = await ps.get_reminder(topic_id)
    assert fetched["id"] == created["id"]

    listed = await ps.list_reminders()
    assert any(r["topic_id"] == topic_id for r in listed["reminders"])

    cancelled = await ps.cancel_reminder(topic_id)
    assert cancelled == {"topic_id": topic_id, "deleted": True}
    assert "error" in await ps.get_reminder(topic_id)


async def test_set_reminder_rejects_bad_datetime_and_missing_topic() -> None:
    await _set_expose('{"reminders": true}')
    bad = await ps.set_reminder(1, "not-a-date")
    assert "error" in bad and "ISO 8601" in bad["error"]

    missing = await ps.set_reminder(999_999, "2999-01-01T09:00:00Z")
    assert "error" in missing and "not found" in missing["error"]


# ---------------------------------------------------------------------------
# chats / agents / live accessors + cross-entity search gating
# ---------------------------------------------------------------------------
async def _make_chat(title: str) -> int:
    from precursor.backend.models import Chat
    from precursor.backend.services.slugs import allocate_unique_slug, slugify

    async with SessionLocal() as session:
        chat = Chat(
            title=title,
            slug=await allocate_unique_slug(session, slugify(title) or "chat", Chat),
        )
        session.add(chat)
        await session.commit()
        return chat.id


async def _make_agent(title: str, *, task_prompt: str = "", result_summary: str = "") -> int:
    from precursor.backend.models import AgentSession

    async with SessionLocal() as session:
        agent = AgentSession(
            title=title, task_prompt=task_prompt, result_summary=result_summary, status="completed"
        )
        session.add(agent)
        await session.commit()
        return agent.id


async def _make_live(title: str, *, notes: str = "", summary: str | None = None) -> int:
    from precursor.backend.models import MeetingSession
    from precursor.backend.services.slugs import allocate_unique_slug, slugify

    async with SessionLocal() as session:
        live = MeetingSession(
            title=title,
            slug=await allocate_unique_slug(session, slugify(title) or "live", MeetingSession),
            notes=notes,
            summary=summary,
        )
        session.add(live)
        await session.commit()
        return live.id


async def test_new_sections_gated_when_off() -> None:
    await _set_expose("{}")
    for result in (
        await ps.list_chats(),
        await ps.get_chat(1),
        await ps.list_chat_messages(1),
        await ps.list_agents(),
        await ps.get_agent(1),
        await ps.list_live_sessions(),
        await ps.get_live_session(1),
    ):
        assert "error" in result
        assert "not exposed" in result["error"]


async def test_chat_accessors() -> None:
    await _set_expose('{"chats": true}')
    chat_id = await _make_chat("MCP chat surface")
    listed = await ps.list_chats(q="MCP chat surface")
    assert any(c["id"] == chat_id for c in listed["chats"])
    fetched = await ps.get_chat(chat_id)
    assert fetched["id"] == chat_id
    msgs = await ps.list_chat_messages(chat_id)
    assert msgs["chat_id"] == chat_id and msgs["count"] == 0


async def test_agent_accessors() -> None:
    await _set_expose('{"agents": true}')
    agent_id = await _make_agent(
        "MCP agent task", task_prompt="do the thing", result_summary="did the thing"
    )
    listed = await ps.list_agents(q="MCP agent task")
    assert any(a["id"] == agent_id for a in listed["agents"])
    fetched = await ps.get_agent(agent_id)
    assert fetched["task_prompt"] == "do the thing"
    assert fetched["result_summary"] == "did the thing"


async def test_live_accessor_returns_full_content() -> None:
    await _set_expose('{"live": true}')
    live_id = await _make_live("MCP live meeting", notes="my notes", summary="the recap")
    listed = await ps.list_live_sessions(q="MCP live meeting")
    assert any(s["id"] == live_id for s in listed["live_sessions"])
    fetched = await ps.get_live_session(live_id)
    assert fetched["notes"] == "my notes"
    assert fetched["summary"] == "the recap"
    assert fetched["transcript"] == []
    assert fetched["insights"] == []


async def test_search_includes_surface_only_when_section_exposed() -> None:
    # Search on, but chats/agents/live off: a chat hit must not leak.
    await _set_expose('{"search": true}')
    await _make_chat("Zzquux searchable chat")
    result = await ps.search("Zzquux")
    assert "error" not in result
    assert all(r["section"] != "chats" for r in result["results"])

    # Expose chats too: now the chat hit appears with its accessor hint.
    await _set_expose('{"search": true, "chats": true}')
    result = await ps.search("Zzquux")
    chat_hits = [r for r in result["results"] if r["section"] == "chats"]
    assert chat_hits
    assert chat_hits[0]["accessor"] == "get_chat / list_chat_messages"


def _mcp_post(client: TestClient, body: dict, session_id: str | None = None) -> object:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    return client.post("/mcp", json=body, headers=headers)


_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}


def _base_url() -> str:
    """The app's own loopback origin, honouring a custom PRECURSOR_PORT/HOST.

    The MCP HTTP transport's Host allowlist is bound to the configured
    host:port, so a request must present a matching Host header. Deriving the
    base URL from settings keeps these tests green when a dev ``.env`` sets a
    non-default port, instead of hardcoding :8000.
    """
    cfg = get_settings()
    return f"http://{cfg.host}:{cfg.port}"


def test_http_transport_404_when_disabled() -> None:
    app = create_app()
    with TestClient(app, base_url=_base_url()) as client:
        r = _mcp_post(client, _INIT)
        assert r.status_code == 404


def test_http_transport_handshake_when_enabled() -> None:
    app = create_app()
    with TestClient(app, base_url=_base_url()) as client:
        client.put("/api/settings", json={"mcp_http_enabled": True})
        r = _mcp_post(client, _INIT)
        assert r.status_code == 200
        sid = r.headers.get("mcp-session-id")
        assert sid
        _mcp_post(
            client,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_id=sid,
        )
        r2 = _mcp_post(
            client,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            session_id=sid,
        )
        assert r2.status_code == 200
        assert "list_topics" in r2.text
        assert "post_message" in r2.text


def test_http_transport_rejects_foreign_host() -> None:
    app = create_app()
    with TestClient(app, base_url=_base_url()) as client:
        client.put("/api/settings", json={"mcp_http_enabled": True})
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Host": "evil.example.com",
        }
        r = client.post("/mcp", json=_INIT, headers=headers)
        # FastMCP's Host allowlist rejects non-localhost Host headers.
        assert r.status_code == 421


def test_http_transport_bare_path_not_405() -> None:
    # Regression: the bare /mcp URL (no trailing slash) must reach the MCP
    # handler, not get shadowed by the SPA catch-all (which produced 405).
    app = create_app()
    with TestClient(app, base_url=_base_url()) as client:
        client.put("/api/settings", json={"mcp_http_enabled": True})
        r = client.post(
            "/mcp",
            json=_INIT,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            follow_redirects=False,
        )
        assert r.status_code == 200


def test_settings_expose_http_fields() -> None:
    app = create_app()
    with TestClient(app, base_url=_base_url()) as client:
        # Reset to the default so this test is independent of other tests' writes.
        client.put("/api/settings", json={"mcp_http_enabled": False})
        s = client.get("/api/settings").json()
        assert s["mcp_http_enabled"] is False
        assert s["mcp_http_loopback_ok"] is True
        assert s["mcp_http_url"] == f"{_base_url()}/mcp"
