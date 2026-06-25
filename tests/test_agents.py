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

        configs, oauth_expiry = await AgentManager()._catalog_mcp_configs()

        # No OAuth-protected server attached here (workiq disabled) → no expiry.
        assert oauth_expiry is None
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


async def test_run_command_rejects_unknown() -> None:
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
