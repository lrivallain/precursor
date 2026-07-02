"""Agents API tests — the non-SDK seams of Agents mode.

The live Copilot SDK runtime can't run here (no subscription / binary in CI), so
these tests cover the HTTP surface that's independent of it: the feature is
opt-in and off by default, so listing is empty and creating a task is refused
until the operator enables it. The settings endpoint advertises the gate.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.main import create_app


def test_agents_disabled_by_default() -> None:
    app = create_app()
    with TestClient(app) as client:
        listed = client.get("/api/agents")
        assert listed.status_code == 200
        assert listed.json() == []

        created = client.post("/api/agents", json={"task": "do a thing"})
        assert created.status_code == 409
        assert "disabled" in created.json()["detail"].lower()


def test_settings_expose_agents_gate() -> None:
    app = create_app()
    with TestClient(app) as client:
        body = client.get("/api/settings").json()
        assert body["agents_enabled"] is False
        # availability is a runtime probe — only the key/type contract matters.
        assert isinstance(body["agents_available"], bool)
        assert isinstance(body["agents_default_model"], str)


def test_enabling_agents_persists_and_is_reported(monkeypatch) -> None:
    # Neutralise the runtime probe so flipping the toggle doesn't try to launch a
    # real Copilot CLI process during the test (manager.start gates on this).
    from precursor.backend.services.agents import runtime

    monkeypatch.setattr(runtime, "agents_available", lambda: (False, "test: disabled"))

    app = create_app()
    with TestClient(app) as client:
        updated = client.put("/api/settings", json={"agents_enabled": True})
        assert updated.status_code == 200
        assert updated.json()["agents_enabled"] is True
        assert client.get("/api/settings").json()["agents_enabled"] is True

        # Reset so the flag doesn't leak into other tests sharing the DB — a later
        # app startup would otherwise try to launch the real Copilot runtime.
        reset = client.put("/api/settings", json={"agents_enabled": False})
        assert reset.json()["agents_enabled"] is False


async def _set_mcp_enabled(mapping: dict[str, bool]) -> None:
    import json

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AppSetting

    async with SessionLocal() as session:
        row = await session.get(AppSetting, "mcp_enabled")
        encoded = json.dumps(mapping)
        if row is None:
            session.add(AppSetting(key="mcp_enabled", value=encoded))
        else:
            row.value = encoded
        await session.commit()


async def test_catalog_mcp_configs_attaches_enabled_servers() -> None:
    """Agents attach enabled catalog servers (built-in + user), never precursor."""
    import pytest

    from precursor.backend.services.agents import runtime
    from precursor.backend.services.agents.manager import AgentManager
    from precursor.backend.services.mcp.client import get_mcp_client_manager

    if not runtime.sdk_installed():
        pytest.skip("github-copilot-sdk not installed")

    # Initialise the schema (alembic upgrade runs on app startup).
    with TestClient(create_app()):
        pass

    manager = get_mcp_client_manager()
    # A user server with stored headers (exercises the secret-folding path) and
    # a malformed stdio user server (no command → skipped, not raised).
    manager.register_user_entry(
        name="my-http",
        transport="streamable_http",
        url="https://example.test/mcp",
        headers={"Authorization": "Bearer secret-token"},
    )
    manager.register_user_entry(
        name="my-broken",
        transport="stdio",
        command=None,
    )
    try:
        # Enable two built-ins (one stdio, one http), the user http server, and
        # the broken one; leave 'workiq' disabled and 'precursor' enabled.
        await _set_mcp_enabled(
            {
                "fetch": True,
                "github": True,
                "workiq": False,
                "my-http": True,
                "my-broken": True,
                "precursor": True,
            }
        )

        configs, oauth_expiry, auth_required = await AgentManager()._catalog_mcp_configs()

        # No OAuth-protected server attached here (workiq disabled) → no expiry,
        # and a disabled server is never a sign-in prompt.
        assert oauth_expiry is None
        assert auth_required == []
        # precursor is attached separately with full access — never here.
        assert "precursor" not in configs
        # Disabled built-in excluded; malformed entry skipped.
        assert "workiq" not in configs
        assert "my-broken" not in configs

        # Built-in stdio server.
        fetch = configs["fetch"]
        assert fetch["type"] == "stdio"
        assert fetch["tools"] == ["*"]

        # Built-in remote server.
        github = configs["github"]
        assert github["type"] == "http"
        assert github["url"] == "https://api.githubcopilot.com/mcp/"

        # User server with its stored Authorization header folded in.
        http = configs["my-http"]
        assert http["type"] == "http"
        assert http["url"] == "https://example.test/mcp"
        assert http["headers"] == {"Authorization": "Bearer secret-token"}
        assert http["tools"] == ["*"]
    finally:
        manager.unregister_user_entry("my-http")
        manager.unregister_user_entry("my-broken")
        await _set_mcp_enabled({})


async def test_oauth_bearer_header_only_applies_to_workiq() -> None:
    """Catalog servers without an OAuth provider never get a bearer header."""
    from precursor.backend.services.agents.manager import AgentManager

    assert await AgentManager()._oauth_bearer_header("github") is None
    assert await AgentManager()._oauth_bearer_header("my-http") is None


async def test_oauth_bearer_header_workiq_injects_token(monkeypatch) -> None:
    """WorkIQ's OAuth token is folded into a static Authorization header."""
    from datetime import UTC, datetime, timedelta

    from precursor.backend.services.agents.manager import AgentManager
    from precursor.backend.services.mcp import workiq_preview as wp

    expires = datetime.now(UTC) + timedelta(hours=1)

    async def _tok() -> tuple[str, datetime]:
        return "wq-access-token", expires

    monkeypatch.setattr(wp, "resolve_workiq_bearer_token", _tok)
    result = await AgentManager()._oauth_bearer_header("workiq")
    assert result == ({"Authorization": "Bearer wq-access-token"}, expires)


async def test_oauth_bearer_header_passes_through_unknown_expiry(monkeypatch) -> None:
    """A resolvable token with unknown lifetime yields a header and a None expiry."""
    from precursor.backend.services.agents.manager import AgentManager
    from precursor.backend.services.mcp import workiq_preview as wp

    async def _tok() -> tuple[str, None]:
        return "wq-access-token", None

    monkeypatch.setattr(wp, "resolve_workiq_bearer_token", _tok)
    result = await AgentManager()._oauth_bearer_header("workiq")
    assert result == ({"Authorization": "Bearer wq-access-token"}, None)


async def test_oauth_bearer_header_workiq_without_token_is_none(monkeypatch) -> None:
    """No stored credentials → no header, so the caller skips attaching WorkIQ."""
    from precursor.backend.services.agents.manager import AgentManager
    from precursor.backend.services.mcp import workiq_preview as wp

    async def _none() -> None:
        return None

    monkeypatch.setattr(wp, "resolve_workiq_bearer_token", _none)
    assert await AgentManager()._oauth_bearer_header("workiq") is None


def test_oauth_stale_refreshes_within_margin() -> None:
    """A live session is stale once its token expiry is inside the refresh margin."""
    from datetime import UTC, datetime, timedelta

    from precursor.backend.services.agents.manager import (
        _OAUTH_REFRESH_MARGIN,
        AgentManager,
        _LiveSession,
    )

    now = datetime.now(UTC)
    fresh = _LiveSession(sdk_session=object(), oauth_expires_at=now + timedelta(hours=1))
    expiring = _LiveSession(
        sdk_session=object(), oauth_expires_at=now + _OAUTH_REFRESH_MARGIN - timedelta(minutes=1)
    )
    no_oauth = _LiveSession(sdk_session=object(), oauth_expires_at=None)

    assert AgentManager._oauth_stale(fresh) is False
    assert AgentManager._oauth_stale(expiring) is True
    # No OAuth server attached → never forced to rebuild.
    assert AgentManager._oauth_stale(no_oauth) is False


async def test_resolve_workiq_bearer_token_without_stored_tokens_is_none() -> None:
    """With no persisted OAuth tokens we return None without opening a session."""
    from precursor.backend.services.mcp.workiq_preview import (
        clear_workiq_oauth_tokens,
        resolve_workiq_bearer_token,
    )

    # Initialise the schema (alembic upgrade runs on app startup).
    with TestClient(create_app()):
        pass

    await clear_workiq_oauth_tokens()
    assert await resolve_workiq_bearer_token() is None


async def test_stored_token_expiry_combines_issue_time_and_lifetime() -> None:
    """set_tokens stamps issue time so we can recover an absolute expiry."""
    from datetime import UTC, datetime

    from mcp.shared.auth import OAuthToken

    from precursor.backend.services.mcp.workiq_preview import (
        DbTokenStorage,
        _stored_token_expiry,
        clear_workiq_oauth_tokens,
    )

    with TestClient(create_app()):
        pass

    await clear_workiq_oauth_tokens()
    storage = DbTokenStorage()

    # No issue stamp yet → expiry is unknown.
    no_stamp = OAuthToken(access_token="t", token_type="Bearer", expires_in=3600)
    assert await _stored_token_expiry(no_stamp) is None

    before = datetime.now(UTC)
    await storage.set_tokens(no_stamp)
    expiry = await _stored_token_expiry(no_stamp)
    assert expiry is not None
    delta = (expiry - before).total_seconds()
    # issued_at ~ now, lifetime 3600s → expiry roughly an hour out.
    assert 3590 <= delta <= 3660

    # A token without a declared lifetime stays unknown even once stamped.
    no_lifetime = OAuthToken(access_token="t", token_type="Bearer")
    assert await _stored_token_expiry(no_lifetime) is None

    await clear_workiq_oauth_tokens()


async def test_catalog_mcp_configs_authenticates_workiq_preview(monkeypatch) -> None:
    """WorkIQ preview is attached with a bearer header, or skipped when signed out."""
    from datetime import UTC, datetime, timedelta

    import httpx
    import pytest

    from precursor.backend.services.agents import runtime
    from precursor.backend.services.agents.manager import _OAUTH_FALLBACK_TTL, AgentManager
    from precursor.backend.services.mcp import workiq_preview as wp
    from precursor.backend.services.mcp.client import get_mcp_client_manager

    if not runtime.sdk_installed():
        pytest.skip("github-copilot-sdk not installed")

    with TestClient(create_app()):
        pass

    manager = get_mcp_client_manager()
    manager.configure_workiq_preview(True, auth_provider=httpx.Auth())
    try:
        await _set_mcp_enabled({"workiq": True})

        expires = datetime.now(UTC) + timedelta(hours=1)

        async def _tok() -> tuple[str, datetime]:
            return "wq-token", expires

        monkeypatch.setattr(wp, "resolve_workiq_bearer_token", _tok)
        configs, oauth_expiry = await AgentManager()._catalog_mcp_configs()
        assert configs["workiq"]["type"] == "http"
        assert configs["workiq"]["url"] == wp.WORKIQ_PREVIEW_URL
        assert configs["workiq"]["headers"] == {"Authorization": "Bearer wq-token"}
        # The token's real expiry is surfaced so the session can refresh in time.
        assert oauth_expiry == expires

        # Unknown lifetime → a conservative fallback TTL, not None.
        async def _tok_no_exp() -> tuple[str, None]:
            return "wq-token", None

        monkeypatch.setattr(wp, "resolve_workiq_bearer_token", _tok_no_exp)
        before = datetime.now(UTC)
        _, fallback_expiry = await AgentManager()._catalog_mcp_configs()
        assert fallback_expiry is not None
        assert before < fallback_expiry <= datetime.now(UTC) + _OAUTH_FALLBACK_TTL

        async def _none() -> None:
            return None

        monkeypatch.setattr(wp, "resolve_workiq_bearer_token", _none)
        configs, oauth_expiry = await AgentManager()._catalog_mcp_configs()
        assert "workiq" not in configs
        assert oauth_expiry is None
    finally:
        manager.configure_workiq_preview(False, auth_provider=None)
        await _set_mcp_enabled({})


def test_parse_agent_command() -> None:
    from precursor.backend.services.agents.manager import parse_agent_command

    assert parse_agent_command("hello there") is None
    assert parse_agent_command("  not a / command") is None
    assert parse_agent_command("/rename New Title") == ("rename", "New Title")
    # Leading whitespace tolerated; name lowercased; argument trimmed.
    assert parse_agent_command("  /Rename   New Title  ") == ("rename", "New Title")
    assert parse_agent_command("/clear") == ("clear", "")
    # Unknown commands still parse (so the caller can reject them by name).
    assert parse_agent_command("/whatever do stuff") == ("whatever", "do stuff")


async def _make_agent(**overrides: object) -> int:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    fields: dict[str, object] = {"title": "Old title", "task_prompt": "seed", "status": "idle"}
    fields.update(overrides)
    async with SessionLocal() as session:
        agent = AgentSession(**fields)
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        return agent.id


async def test_run_command_rename() -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.agents.manager import AgentManager

    with TestClient(create_app()):
        pass
    agent_id = await _make_agent()

    await AgentManager().run_command(agent_id, "rename", "  Shiny   New   Name ")
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        assert agent.title == "Shiny New Name"


async def test_run_command_rename_requires_argument() -> None:
    import pytest

    from precursor.backend.services.agents.manager import AgentManager

    with TestClient(create_app()):
        pass
    agent_id = await _make_agent()

    with pytest.raises(ValueError, match="Usage: /rename"):
        await AgentManager().run_command(agent_id, "rename", "   ")


async def test_run_command_archive() -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.agents.manager import AgentManager

    with TestClient(create_app()):
        pass
    agent_id = await _make_agent()

    await AgentManager().run_command(agent_id, "archive", "")
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        assert agent.archived_at is not None


async def test_run_command_clear_resets_session_and_timeline() -> None:
    from sqlalchemy import select

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentEventRecord, AgentSession
    from precursor.backend.services.agents.manager import AgentManager

    with TestClient(create_app()):
        pass
    agent_id = await _make_agent(
        copilot_session_id="sess-123",
        status="completed",
        active_prompt="in flight",
        result_summary="done",
        error="boom",
    )
    # Seed an archived timeline event that clear should wipe.
    async with SessionLocal() as session:
        session.add(AgentEventRecord(agent_session_id=agent_id, payload='{"kind":"assistant"}'))
        await session.commit()

    await AgentManager().run_command(agent_id, "clear", "")

    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        # A fresh SDK session id is minted (so the next turn starts clean) — never
        # left null, and never the previous handle.
        assert agent.copilot_session_id is not None
        assert agent.copilot_session_id != "sess-123"
        assert agent.status == "idle"
        assert agent.active_prompt is None
        assert agent.result_summary is None
        assert agent.error is None
        remaining = (
            await session.scalars(
                select(AgentEventRecord).where(AgentEventRecord.agent_session_id == agent_id)
            )
        ).all()
        assert remaining == []


async def test_clear_session_keep_id_preserves_uuid_and_deletes_sdk_state() -> None:
    """``keep_id=True`` keeps the public uuid so a scheduled ``/agent <uuid>``
    nudge keeps resolving, and deletes the SDK's on-disk state so the next turn
    starts from a clean context instead of resuming the old transcript."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.agents.manager import AgentManager

    with TestClient(create_app()):
        pass
    agent_id = await _make_agent(
        copilot_session_id="sess-keep",
        status="completed",
        active_prompt="in flight",
        result_summary="done",
        error="boom",
    )

    deleted: list[str] = []

    class _FakeClient:
        async def delete_session(self, session_id: str) -> None:
            deleted.append(session_id)

    mgr = AgentManager()
    mgr._client = _FakeClient()  # type: ignore[assignment]

    await mgr.clear_session(agent_id, keep_id=True)

    assert deleted == ["sess-keep"]
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        # Same public handle — the schedule's "/agent <uuid>" keeps targeting it.
        assert agent.copilot_session_id == "sess-keep"
        assert agent.status == "idle"
        assert agent.active_prompt is None
        assert agent.result_summary is None
        assert agent.error is None


async def test_rerun_task_clears_then_replays_task_prompt() -> None:
    """`rerun_task` resets the context (same uuid) and re-delivers task_prompt,
    appending an optional one-off note for the run."""
    from precursor.backend.services.agents.manager import AgentManager

    with TestClient(create_app()):
        pass
    agent_id = await _make_agent(task_prompt="Process the inbox.", copilot_session_id="sess-run")

    calls: dict[str, object] = {}

    async def fake_clear(aid, *, keep_id=False):  # type: ignore[no-untyped-def]
        calls["clear"] = (aid, keep_id)

    async def fake_send(aid, text):  # type: ignore[no-untyped-def]
        calls["send"] = (aid, text)

    mgr = AgentManager()
    mgr.clear_session = fake_clear  # type: ignore[assignment]
    mgr.send_message = fake_send  # type: ignore[assignment]

    await mgr.rerun_task(agent_id)
    assert calls["clear"] == (agent_id, True)
    assert calls["send"] == (agent_id, "Process the inbox.")

    await mgr.rerun_task(agent_id, extra="prioritise FR mail")
    assert calls["send"] == (agent_id, "Process the inbox.\n\nprioritise FR mail")


async def test_notify_back_posts_full_answer_not_truncated_summary() -> None:
    """The exchange reposted to the linked topic carries the agent's *full*
    answer, even when it exceeds the 2000-char ``result_summary`` cap used for
    the agent list. Regression: the topic message was previously truncated."""
    from sqlalchemy import select

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession, Message, Topic
    from precursor.backend.models.message import MessageRole
    from precursor.backend.services.agents.manager import AgentManager, _LiveSession

    with TestClient(create_app()):
        pass

    async with SessionLocal() as session:
        topic = Topic(title="Briefing", slug="briefing-notify-back")
        session.add(topic)
        await session.commit()
        await session.refresh(topic)
        topic_id = topic.id

    long_answer = "A long briefing. " * 300  # well over 2000 chars
    assert len(long_answer) > 2000

    agent_id = await _make_agent(topic_id=topic_id, result_summary=long_answer[:2000])

    mgr = AgentManager()
    live = _LiveSession(sdk_session=None)
    live.pending_prompt = "Run the briefing"
    live.pending_answer = long_answer
    mgr._live[agent_id] = live

    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        await mgr._notify_back(agent)

    async with SessionLocal() as session:
        rows = (
            await session.scalars(
                select(Message).where(Message.topic_id == topic_id).order_by(Message.id)
            )
        ).all()
    assistant = [m for m in rows if m.role == MessageRole.ASSISTANT]
    assert len(assistant) == 1
    # Full content preserved — not capped to the 2000-char summary.
    assert assistant[0].content == long_answer.strip()
    # The pending answer is consumed so a repeated idle event won't double-post.
    assert live.pending_answer is None

    import pytest

    from precursor.backend.services.agents.manager import AgentManager

    with TestClient(create_app()):
        pass
    agent_id = await _make_agent()

    with pytest.raises(ValueError, match="isn't available"):
        await AgentManager().run_command(agent_id, "role", "assistant")


def test_agent_command_registry_is_source_of_truth() -> None:
    """The registry keys drive both validation and the rejection message."""
    from precursor.backend.services.agents.manager import AgentManager

    assert set(AgentManager.supported_commands()) == {
        "rename",
        "archive",
        "clear",
        "memory-store",
        "memory-update",
    }
    assert set(AgentManager._COMMAND_HANDLERS) == set(AgentManager.supported_commands())


def test_normalise_usage_event_captures_token_counts() -> None:
    """`AssistantUsageData` rounds surface their tokens so the side panel can
    aggregate per-agent usage from the timeline."""
    from precursor.backend.services.agents.manager import AgentManager

    class AssistantUsageData:
        def __init__(self) -> None:
            self.model = "gpt-x"
            self.input_tokens = 1200
            self.output_tokens = 340
            self.reasoning_tokens = 50

    event = AgentManager()._normalise(AssistantUsageData())

    assert event.kind == "usage"
    assert event.data is not None
    assert event.data["input_tokens"] == 1200
    assert event.data["output_tokens"] == 340
    assert event.data["reasoning_tokens"] == 50
    # The resolved model is captured so the UI can label each turn's answer.
    assert event.data["model"] == "gpt-x"
    # Stored as raw ints (not JSON-stringified) so the UI can do arithmetic.
    assert isinstance(event.data["input_tokens"], int)


def test_normalise_context_usage_event_captures_window() -> None:
    """`SessionUsageInfoData` maps to a ``context_usage`` step carrying the live
    context-window occupancy for the side-panel progress bar."""
    from precursor.backend.services.agents.manager import AgentManager

    class SessionUsageInfoData:
        def __init__(self) -> None:
            self.current_tokens = 8000
            self.token_limit = 128000
            self.conversation_tokens = 7500

    event = AgentManager()._normalise(SessionUsageInfoData())

    assert event.kind == "context_usage"
    assert event.data is not None
    assert event.data["current_tokens"] == 8000
    assert event.data["token_limit"] == 128000
    assert event.data["conversation_tokens"] == 7500


async def test_update_agent_title_only_needs_no_runtime() -> None:
    """A title-only PATCH never touches the runtime (no task replay)."""
    with TestClient(create_app()):
        pass
    agent_id = await _make_agent(title="Old", task_prompt="seed", status="idle")

    with TestClient(create_app()) as client:
        resp = client.patch(f"/api/agents/{agent_id}", json={"title": "New name"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "New name"
        assert body["task_prompt"] == "seed"


async def test_update_agent_task_requires_runtime() -> None:
    """Editing the task replays it, so it's gated on Agents mode being usable."""
    with TestClient(create_app()):
        pass
    agent_id = await _make_agent(task_prompt="old", status="idle")

    with TestClient(create_app()) as client:
        resp = client.patch(f"/api/agents/{agent_id}", json={"task": "new instructions"})
        assert resp.status_code == 409
        assert "disabled" in resp.json()["detail"].lower()


async def test_update_agent_task_rejected_while_running() -> None:
    """The task can't be replayed under an in-flight turn — rejected before any work."""
    with TestClient(create_app()):
        pass
    agent_id = await _make_agent(task_prompt="old", status="running")

    with TestClient(create_app()) as client:
        resp = client.patch(f"/api/agents/{agent_id}", json={"task": "new"})
        assert resp.status_code == 409
        assert "stop the agent" in resp.json()["detail"].lower()


async def test_restart_with_task_replays_and_keeps_session_id() -> None:
    """Re-seeding drops the live session and replays the task, never minting a new
    ``copilot_session_id`` (which would break scheduled ``/agent <uuid>`` nudges)."""
    from precursor.backend.services.agents.manager import AgentManager

    mgr = AgentManager()
    calls: list[tuple[str, int, bool]] = []

    async def fake_teardown(agent_id: int, *, forget: bool = False) -> None:
        calls.append(("teardown", agent_id, forget))

    async def fake_start(agent_id: int) -> None:
        calls.append(("start", agent_id, False))

    mgr.teardown_session = fake_teardown  # type: ignore[method-assign]
    mgr.start_task = fake_start  # type: ignore[method-assign]

    await mgr.restart_with_task(7)

    assert calls == [("teardown", 7, False), ("start", 7, False)]


async def test_agent_unread_lifecycle() -> None:
    """The Agents list carries an unread badge for background assistant replies.

    Mirrors the topic/chat unread model: a session is fully read until opened,
    only assistant replies (not tool/reasoning steps) count, and marking it read
    clears the badge. The SDK can't run in CI, so we seed rows directly.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentEventRecord, AgentSession
    from precursor.backend.schemas.agent import AgentEvent

    def reply(text: str) -> str:
        return AgentEvent(kind="assistant_message", text=text).model_dump_json()

    with TestClient(create_app()) as client:
        async with SessionLocal() as session:
            agent = AgentSession(title="Background agent", task_prompt="do", status="idle")
            session.add(agent)
            await session.flush()
            aid = agent.id
            # A reply that predates any "open" — last_read_at is null, so it must
            # be treated as fully read (no retroactive unread).
            session.add(AgentEventRecord(agent_session_id=aid, payload=reply("hello")))
            await session.commit()

        row = next(a for a in client.get("/api/agents").json() if a["id"] == aid)
        assert row["unread_count"] == 0

        # Open it, then a fresh reply (and a tool step that must NOT count) land.
        assert client.post(f"/api/agents/{aid}/read").status_code == 204
        async with SessionLocal() as session:
            session.add(AgentEventRecord(agent_session_id=aid, payload=reply("done")))
            session.add(
                AgentEventRecord(
                    agent_session_id=aid,
                    payload=AgentEvent(kind="tool_call", tool_name="shell").model_dump_json(),
                )
            )
            await session.commit()

        row = next(a for a in client.get("/api/agents").json() if a["id"] == aid)
        assert row["unread_count"] == 1
        assert client.get(f"/api/agents/{aid}").json()["unread_count"] == 1

        # Reading again clears the badge.
        assert client.post(f"/api/agents/{aid}/read").status_code == 204
        row = next(a for a in client.get("/api/agents").json() if a["id"] == aid)
        assert row["unread_count"] == 0


async def test_agent_notify_back_marks_never_opened_container_unread() -> None:
    """An agent posting into a linked topic/chat lights the unread badge even if
    the container was never opened.

    A conversation with ``last_read_at = NULL`` is treated as fully read, so the
    agent's reply would otherwise not count. ``_notify_back`` pins ``last_read_at``
    just before the posted messages (mirroring the reminder ticker) so the badge
    shows reliably.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession, Topic
    from precursor.backend.services.agents.manager import AgentManager, _LiveSession

    def find(nodes: list[dict], tid: int) -> dict | None:
        for n in nodes:
            if n["id"] == tid:
                return n
            hit = find(n.get("children", []), tid)
            if hit is not None:
                return hit
        return None

    with TestClient(create_app()) as client:
        async with SessionLocal() as session:
            topic = Topic(title="Bg topic", slug="bg-topic-notify-back")
            session.add(topic)
            await session.flush()
            tid = topic.id
            assert topic.last_read_at is None  # never opened
            agent = AgentSession(title="A", task_prompt="do", status="idle", topic_id=tid)
            session.add(agent)
            await session.commit()
            aid = agent.id

        mgr = AgentManager()
        mgr._live[aid] = _LiveSession(
            sdk_session=None, pending_prompt="do the thing", pending_answer="all done"
        )
        async with SessionLocal() as session:
            agent = await session.get(AgentSession, aid)
            assert agent is not None
            await mgr._notify_back(agent)

        # The reply landed and the never-opened topic now reads as unread.
        node = find(client.get("/api/topics/tree").json(), tid)
        assert node is not None
        assert node["unread_count"] == 1

        async with SessionLocal() as session:
            refreshed = await session.get(Topic, tid)
            assert refreshed is not None
            assert refreshed.last_read_at is not None


async def test_agent_background_task_events_broadcast_to_originating_tab() -> None:
    """Events published from agent background work carry no client id.

    ``enqueue``/``_spawn`` runs agent work in a task whose context has the
    request's ``X-Client-Id`` cleared, so the notify-back unread (and live
    progress) reaches *every* tab — including the one that started the agent,
    which would otherwise echo-suppress its own event.
    """
    import asyncio

    from precursor.backend.services import events
    from precursor.backend.services.agents.manager import AgentManager

    mgr = AgentManager()

    async def publisher() -> None:
        await events.get_bus().publish({"type": "agent.changed", "agent_session_id": 1})

    try:
        async with events.get_bus().subscribe() as q:
            events.set_current_client_id("tab-A")  # as request middleware would
            mgr.enqueue(publisher())
            evt = await asyncio.wait_for(q.get(), timeout=2)
        assert evt["client_id"] is None  # broadcast, not stamped with tab-A
    finally:
        events.set_current_client_id(None)


async def test_mark_read_endpoints_publish_read_changed() -> None:
    """The topic/chat/agent /read endpoints emit a ``read.changed`` event so
    other tabs clear the badge + counter for that discussion in real time."""
    import asyncio

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession, Chat, Topic
    from precursor.backend.routers.agents import mark_agent_read
    from precursor.backend.routers.chats import mark_chat_read
    from precursor.backend.routers.topics import mark_topic_read
    from precursor.backend.services import events

    with TestClient(create_app()):
        pass

    async with SessionLocal() as session:
        chat = Chat(title="c", slug="read-evt-chat")
        topic = Topic(title="t", slug="read-evt-topic")
        agent = AgentSession(title="a", task_prompt="x", status="idle")
        session.add_all([chat, topic, agent])
        await session.commit()
        cid, tid, aid = chat.id, topic.id, agent.id

    async with events.get_bus().subscribe() as q:
        async with SessionLocal() as session:
            await mark_chat_read(cid, session=session)
            await mark_topic_read(tid, session=session)
            await mark_agent_read(str(aid), session=session)
        seen = [await asyncio.wait_for(q.get(), timeout=2) for _ in range(3)]

    by_kind = {(e.get("chat_id"), e.get("topic_id"), e.get("agent_session_id")): e for e in seen}
    assert all(e["type"] == "read.changed" for e in seen)
    assert (cid, None, None) in by_kind
    assert (None, tid, None) in by_kind
    assert (None, None, aid) in by_kind

    # Clean up so the shared session DB stays empty for order-independent tests
    # (test_app.py asserts an empty chat list to start).
    async with SessionLocal() as session:
        for model, oid in ((Chat, cid), (Topic, tid), (AgentSession, aid)):
            row = await session.get(model, oid)
            if row is not None:
                await session.delete(row)
        await session.commit()
