"""Agents API tests — the non-SDK seams of Agents mode.

The live Copilot SDK runtime can't run here (no subscription / binary in CI), so
these tests cover the HTTP surface that's independent of it: the feature is
opt-in and off by default, so listing is empty and creating a task is refused
until the operator enables it. The settings endpoint advertises the gate.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from precursor.backend.config import get_settings
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
    await _ensure_schema()

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


async def test_enabled_catalog_fingerprint_tracks_toggles() -> None:
    """The fingerprint reflects enabled+registered servers and excludes precursor.

    It drives the rebuild-on-change decision in ``_ensure_live``, so it must
    change when a toggle flips and ignore servers that aren't registered — never
    depending on SDK/credential availability (no runtime needed here).
    """
    from precursor.backend.services.agents.manager import AgentManager
    from precursor.backend.services.mcp.client import get_mcp_client_manager

    await _ensure_schema()

    manager = get_mcp_client_manager()
    manager.register_user_entry(
        name="my-http",
        transport="streamable_http",
        url="https://example.test/mcp",
    )
    mgr = AgentManager()
    try:
        await _set_mcp_enabled({"fetch": True, "precursor": True, "my-http": False})
        fp = await mgr._enabled_catalog_fingerprint()
        # Enabled + registered only; precursor excluded; disabled excluded.
        assert "fetch" in fp
        assert "precursor" not in fp
        assert "my-http" not in fp
        # An enabled toggle that isn't registered doesn't leak in.
        assert "ghost-server" not in fp

        # Toggling a server on changes the fingerprint → triggers a rebuild.
        await _set_mcp_enabled({"fetch": True, "precursor": True, "my-http": True})
        fp2 = await mgr._enabled_catalog_fingerprint()
        assert fp2 != fp
        assert "my-http" in fp2
    finally:
        manager.unregister_user_entry("my-http")
        await _set_mcp_enabled({})


async def test_oauth_bearer_header_skips_non_oauth_servers() -> None:
    """Catalog servers without an OAuth provider never get a bearer header."""
    from precursor.backend.services.agents.manager import AgentManager

    assert await AgentManager()._oauth_bearer_header("github") is None
    assert await AgentManager()._oauth_bearer_header("my-http") is None


async def _enable_preview(monkeypatch) -> None:
    """Turn preview mode on so ``workiq`` resolves to a signable OAuth profile."""
    from precursor.backend.services.mcp import workiq_preview as wp

    async def _on() -> bool:
        return True

    monkeypatch.setattr(wp, "resolve_workiq_preview", _on)


async def test_oauth_bearer_header_workiq_injects_token(monkeypatch) -> None:
    """WorkIQ's OAuth token is folded into a static Authorization header."""
    from datetime import UTC, datetime, timedelta

    from precursor.backend.services.agents.manager import AgentManager
    from precursor.backend.services.mcp import workiq_preview as wp

    expires = datetime.now(UTC) + timedelta(hours=1)

    async def _tok(_profile: object = None) -> tuple[str, datetime]:
        return "wq-access-token", expires

    await _enable_preview(monkeypatch)
    monkeypatch.setattr(wp, "resolve_workiq_bearer_token", _tok)
    result = await AgentManager()._oauth_bearer_header("workiq")
    assert result == ({"Authorization": "Bearer wq-access-token"}, expires)


async def test_oauth_bearer_header_resolves_agent365_servers(monkeypatch) -> None:
    """Agent 365 servers authenticate too — the header isn't preview-only.

    Regression guard: these used to be rejected by name, so enabling one left the
    agent without its tools *and* raised a sign-in prompt that signing in could
    never clear, because the same name gate blocked the retry.
    """
    from precursor.backend.services.agents.manager import AgentManager
    from precursor.backend.services.mcp import agent365
    from precursor.backend.services.mcp import workiq_preview as wp

    name = agent365.AGENT365_SERVERS[0].name
    seen: list[str] = []

    async def _profile(server: str):
        return agent365.build_profile(server, "11111111-2222-3333-4444-555555555555")

    async def _tok(profile: object = None) -> tuple[str, None]:
        seen.append(getattr(profile, "server", ""))
        return "a365-token", None

    monkeypatch.setattr(agent365, "profile_for", _profile)
    monkeypatch.setattr(wp, "resolve_workiq_bearer_token", _tok)

    result = await AgentManager()._oauth_bearer_header(name)
    assert result is not None
    headers, expiry = result
    assert expiry is None
    assert headers["Authorization"].endswith("a365-token")
    # The token is minted against that server's own profile, not the preview one.
    assert seen == [name]


async def test_oauth_bearer_header_passes_through_unknown_expiry(monkeypatch) -> None:
    """A resolvable token with unknown lifetime yields a header and a None expiry."""
    from precursor.backend.services.agents.manager import AgentManager
    from precursor.backend.services.mcp import workiq_preview as wp

    async def _tok(_profile: object = None) -> tuple[str, None]:
        return "wq-access-token", None

    await _enable_preview(monkeypatch)
    monkeypatch.setattr(wp, "resolve_workiq_bearer_token", _tok)
    result = await AgentManager()._oauth_bearer_header("workiq")
    assert result == ({"Authorization": "Bearer wq-access-token"}, None)


async def test_oauth_bearer_header_workiq_without_token_is_none(monkeypatch) -> None:
    """No stored credentials → no header, so the caller skips attaching WorkIQ."""
    from precursor.backend.services.agents.manager import AgentManager
    from precursor.backend.services.mcp import workiq_preview as wp

    async def _none(_profile: object = None) -> None:
        return None

    await _enable_preview(monkeypatch)
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
    await _ensure_schema()

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

    await _ensure_schema()

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

    await _ensure_schema()

    manager = get_mcp_client_manager()
    manager.configure_workiq_preview(True, auth_provider=httpx.Auth())
    try:
        await _set_mcp_enabled({"workiq": True})

        expires = datetime.now(UTC) + timedelta(hours=1)

        async def _tok(_profile: object = None) -> tuple[str, datetime]:
            return "wq-token", expires

        await _enable_preview(monkeypatch)
        monkeypatch.setattr(wp, "resolve_workiq_bearer_token", _tok)
        configs, oauth_expiry, _auth_required = await AgentManager()._catalog_mcp_configs()
        assert configs["workiq"]["type"] == "http"
        assert configs["workiq"]["url"] == wp.WORKIQ_PREVIEW_URL
        assert configs["workiq"]["headers"] == {"Authorization": "Bearer wq-token"}
        # The token's real expiry is surfaced so the session can refresh in time.
        assert oauth_expiry == expires

        # Unknown lifetime → a conservative fallback TTL, not None.
        async def _tok_no_exp(_profile: object = None) -> tuple[str, None]:
            return "wq-token", None

        monkeypatch.setattr(wp, "resolve_workiq_bearer_token", _tok_no_exp)
        before = datetime.now(UTC)
        _, fallback_expiry, _ = await AgentManager()._catalog_mcp_configs()
        assert fallback_expiry is not None
        assert before < fallback_expiry <= datetime.now(UTC) + _OAUTH_FALLBACK_TTL

        async def _none(_profile: object = None) -> None:
            return None

        monkeypatch.setattr(wp, "resolve_workiq_bearer_token", _none)
        configs, oauth_expiry, _auth_required = await AgentManager()._catalog_mcp_configs()
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

    await _ensure_schema()
    agent_id = await _make_agent()

    await AgentManager().run_command(agent_id, "rename", "  Shiny   New   Name ")
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        assert agent.title == "Shiny New Name"


async def test_run_command_rename_requires_argument() -> None:
    import pytest

    from precursor.backend.services.agents.manager import AgentManager

    await _ensure_schema()
    agent_id = await _make_agent()

    with pytest.raises(ValueError, match="Usage: /rename"):
        await AgentManager().run_command(agent_id, "rename", "   ")


async def test_approval_policy_per_agent_override_beats_global_default() -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.agents.manager import AgentManager
    from precursor.backend.services.app_settings import resolve_agents_approval_policy

    await _ensure_schema()

    async with SessionLocal() as session:
        global_default = await resolve_agents_approval_policy(session)
    # Pick an override that differs from the global default so the assertion
    # actually proves the override took effect.
    override = "autonomous" if global_default != "autonomous" else "manual"

    manager = AgentManager()

    # No override → inherits the global default.
    inherit_id = await _make_agent(approval_policy=None)
    async with SessionLocal() as session:
        inherit_agent = await session.get(AgentSession, inherit_id)
    assert await manager._approval_policy(inherit_agent) == global_default

    # Explicit override → wins over the global default.
    override_id = await _make_agent(approval_policy=override)
    async with SessionLocal() as session:
        override_agent = await session.get(AgentSession, override_id)
    assert await manager._approval_policy(override_agent) == override

    # No agent at all → still resolves to the global default.
    assert await manager._approval_policy(None) == global_default


async def test_run_command_archive() -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.agents.manager import AgentManager

    await _ensure_schema()
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

    await _ensure_schema()
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

    await _ensure_schema()
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

    await _ensure_schema()
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

    await _ensure_schema()

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

    await _ensure_schema()
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
    from precursor.backend.services.agents.event_normalizer import normalize_event

    class AssistantUsageData:
        def __init__(self) -> None:
            self.model = "gpt-x"
            self.input_tokens = 1200
            self.output_tokens = 340
            self.reasoning_tokens = 50

    event = normalize_event(AssistantUsageData())

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
    from precursor.backend.services.agents.event_normalizer import normalize_event

    class SessionUsageInfoData:
        def __init__(self) -> None:
            self.current_tokens = 8000
            self.token_limit = 128000
            self.conversation_tokens = 7500

    event = normalize_event(SessionUsageInfoData())

    assert event.kind == "context_usage"
    assert event.data is not None
    assert event.data["current_tokens"] == 8000
    assert event.data["token_limit"] == 128000
    assert event.data["conversation_tokens"] == 7500


def test_normalise_tool_completion_failure_captures_error() -> None:
    """A failed tool must archive *why* it failed, not ``data: null``.

    ``ToolExecutionCompleteData`` reports the outcome via a ``success`` flag and
    a nested ``error`` object (no flat ``is_error``/``error_type`` attrs), so the
    normaliser has to pull those out — otherwise the timeline loses the reason
    the agent hit a wall (the exact bug that made an agent claim it was "blocked").
    """
    from precursor.backend.services.agents.event_normalizer import normalize_event

    class _Err:
        message = "connect timeout after 30s"
        code = "ETIMEDOUT"

    class ToolExecutionCompleteData:
        def __init__(self) -> None:
            self.success = False
            self.tool_call_id = "call-1"
            self.error = _Err()
            self.result = None
            self.sandboxed = True

    event = normalize_event(ToolExecutionCompleteData())

    assert event.tool_status == "error"
    assert event.request_id == "call-1"
    assert event.data is not None
    assert event.data["success"] is False
    assert event.data["error"] == "connect timeout after 30s"
    assert event.data["error_code"] == "ETIMEDOUT"
    # Running in the ephemeral cmd-runner jail is surfaced so silent-non-persist
    # writes are diagnosable.
    assert event.data["sandboxed"] is True


def test_normalise_tool_completion_success_captures_result() -> None:
    """A successful tool archives its (capped) output and a ``done`` status."""
    from precursor.backend.services.agents.event_normalizer import normalize_event

    class _Result:
        content = "x" * 10000
        detailed_content = None

    class _Desc:
        name = "fetch-http_get"

    class ToolExecutionCompleteData:
        def __init__(self) -> None:
            self.success = True
            self.tool_call_id = "call-2"
            self.error = None
            self.result = _Result()
            self.tool_description = _Desc()

    event = normalize_event(ToolExecutionCompleteData())

    assert event.tool_status == "done"
    assert event.data is not None
    assert event.data["success"] is True
    # Output is captured but capped so a huge fetch can't bloat the archive.
    assert event.data["result"].startswith("x")
    assert len(event.data["result"]) <= 4000
    # Completion events have no ``tool_name`` — fall back to the description.
    assert event.tool_name == "fetch-http_get"


async def test_update_agent_title_only_needs_no_runtime() -> None:
    """A title-only PATCH never touches the runtime (no task replay)."""
    await _ensure_schema()
    agent_id = await _make_agent(title="Old", task_prompt="seed", status="idle")

    with TestClient(create_app()) as client:
        resp = client.patch(f"/api/agents/{agent_id}", json={"title": "New name"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "New name"
        assert body["task_prompt"] == "seed"


async def test_update_agent_task_requires_runtime() -> None:
    """Editing the task primes it (session teardown), so it's gated on Agents
    mode being usable."""
    await _ensure_schema()
    agent_id = await _make_agent(task_prompt="old", status="idle")

    with TestClient(create_app()) as client:
        resp = client.patch(f"/api/agents/{agent_id}", json={"task": "new instructions"})
        assert resp.status_code == 409
        assert "disabled" in resp.json()["detail"].lower()


async def test_update_agent_task_rejected_while_running() -> None:
    """The task can't be replayed under an in-flight turn — rejected before any work."""
    await _ensure_schema()
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


async def test_mark_read_state_endpoints_publish_read_changed() -> None:
    """Read and unread state changes notify other tabs in real time."""
    import asyncio

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import (
        AgentEventRecord,
        AgentSession,
        Chat,
        Message,
        MessageRole,
        Topic,
    )
    from precursor.backend.routers.agents import mark_agent_read, mark_agent_unread
    from precursor.backend.routers.chats import mark_chat_read, mark_chat_unread
    from precursor.backend.routers.topics import mark_topic_read, mark_topic_unread
    from precursor.backend.services import events

    await _ensure_schema()

    async with SessionLocal() as session:
        chat = Chat(title="c", slug="read-evt-chat")
        topic = Topic(title="t", slug="read-evt-topic")
        agent = AgentSession(title="a", task_prompt="x", status="idle")
        session.add_all([chat, topic, agent])
        await session.flush()
        session.add_all(
            [
                Message(chat_id=chat.id, role=MessageRole.ASSISTANT, content="chat reply"),
                Message(topic_id=topic.id, role=MessageRole.ASSISTANT, content="topic reply"),
                AgentEventRecord(
                    agent_session_id=agent.id,
                    payload='{"kind":"assistant_message","text":"agent reply"}',
                ),
            ]
        )
        await session.commit()
        cid, tid, aid = chat.id, topic.id, agent.id

    async with events.get_bus().subscribe() as q:
        async with SessionLocal() as session:
            await mark_chat_read(cid, session=session)
            await mark_topic_read(tid, session=session)
            await mark_agent_read(str(aid), session=session)
            await mark_chat_unread(cid, session=session)
            await mark_topic_unread(tid, session=session)
            await mark_agent_unread(str(aid), session=session)
        seen = [await asyncio.wait_for(q.get(), timeout=2) for _ in range(6)]

    assert all(e["type"] == "read.changed" for e in seen)
    assert sum(e.get("chat_id") == cid for e in seen) == 2
    assert sum(e.get("topic_id") == tid for e in seen) == 2
    assert sum(e.get("agent_session_id") == aid for e in seen) == 2

    with TestClient(create_app()) as client:
        chat_row = next(row for row in client.get("/api/chats").json() if row["id"] == cid)
        topic_row = next(row for row in client.get("/api/topics/tree").json() if row["id"] == tid)
        agent_row = next(row for row in client.get("/api/agents").json() if row["id"] == aid)
        assert chat_row["unread_count"] == 1
        assert topic_row["unread_count"] == 1
        assert agent_row["unread_count"] == 1

    # Clean up so the shared session DB stays empty for order-independent tests
    # (test_app.py asserts an empty chat list to start).
    async with SessionLocal() as session:
        for model, oid in ((Chat, cid), (Topic, tid), (AgentSession, aid)):
            row = await session.get(model, oid)
            if row is not None:
                await session.delete(row)
        await session.commit()


async def test_stop_silences_sdk_broken_pipe_teardown_noise() -> None:
    """Clean Ctrl+C shutdown must not spew the SDK's broken-pipe traceback.

    The Copilot CLI child shares our process group, so on Ctrl+C it takes the
    same SIGINT and can close its stdin before our graceful ``client.stop()``
    finishes writing the ``runtime.shutdown`` request. The SDK logs that write
    failure at WARNING with a full traceback (via ``copilot._jsonrpc``) and then
    re-raises it — which the manager already suppresses. ``AgentManager.stop``
    must quiet that expected teardown log so the shell exit stays clean, while
    leaving broken-pipe warnings outside the teardown window untouched.
    """
    import logging

    from precursor.backend.services.agents.manager import AgentManager

    sdk_logger = logging.getLogger("copilot._jsonrpc")
    captured: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    collector = _Collector()
    sdk_logger.addHandler(collector)
    # Pin the shared logger's state so suite-wide logging config (level bumps,
    # ``logging.disable``) can't gate out the WARNING we assert on.
    prev_level, prev_disabled = sdk_logger.level, sdk_logger.disabled
    sdk_logger.setLevel(logging.DEBUG)
    sdk_logger.disabled = False

    class _FakeClient:
        async def stop(self) -> None:
            # Mirror ``copilot._jsonrpc.request``: a write that lost the pipe race
            # is logged at WARNING with the BrokenPipeError attached, then re-raised.
            try:
                raise BrokenPipeError(32, "Broken pipe")
            except BrokenPipeError:
                sdk_logger.warning("JsonRpcClient.request JSON-RPC request finished", exc_info=True)
                raise

    try:
        mgr = AgentManager()
        mgr._ready = True  # type: ignore[attr-defined]
        mgr._client = _FakeClient()  # type: ignore[assignment]

        await mgr.stop()

        # The manager suppressed the re-raised error and tore the client down.
        assert mgr._client is None  # type: ignore[attr-defined]
        # ...and the broken-pipe traceback was filtered out during teardown.
        assert captured == []

        # Control: outside the teardown window the same warning is *not* silenced,
        # proving the filter is scoped and selective (real pipe errors stay visible).
        try:
            raise BrokenPipeError(32, "Broken pipe")
        except BrokenPipeError:
            sdk_logger.warning("post-teardown broken pipe", exc_info=True)
        assert [r.getMessage() for r in captured] == ["post-teardown broken pipe"]
    finally:
        sdk_logger.removeHandler(collector)
        sdk_logger.setLevel(prev_level)
        sdk_logger.disabled = prev_disabled


def _tool_event(
    kind: str,
    *,
    request_id: str | None = None,
    tool_name: str | None = None,
    tool_status: str | None = None,
    text: str | None = None,
):
    """Build a minimal normalised event for the live-activity snapshot test."""
    from precursor.backend.schemas.agent import AgentEvent

    return AgentEvent(
        kind=kind,
        tool_name=tool_name,
        tool_status=tool_status,
        request_id=request_id,
        text=text,
    )


def test_live_activity_snapshots_parallel_tools_and_resets_per_turn() -> None:
    """``live_activity`` derives the in-flight tool, parallel fan-out count, and
    resets running state at turn boundaries (a dropped completion can't leak)."""
    from precursor.backend.services.agents.manager import AgentManager

    mgr = AgentManager()
    # Agent 1: two tools started in parallel, the first finishes → one still running.
    mgr._events[1] = [  # type: ignore[attr-defined]
        _tool_event("turn_start"),
        _tool_event("tool_call", request_id="a", tool_name="grep", tool_status="running"),
        _tool_event("tool_call", request_id="b", tool_name="view", tool_status="running"),
        _tool_event("tool_result", request_id="a", tool_status="done"),
    ]
    # Agent 2: a tool starts but never completes, then the turn ends → reset.
    mgr._events[2] = [  # type: ignore[attr-defined]
        _tool_event("tool_call", request_id="x", tool_name="bash", tool_status="running"),
        _tool_event("turn_end"),
    ]
    # Agent 4: streaming commentary (deltas) is distilled into live narration,
    # preferred over the last completed message and stripped of directives.
    mgr._events[4] = [  # type: ignore[attr-defined]
        _tool_event("turn_start"),
        _tool_event("assistant_message", text="An earlier full message."),
        _tool_event("assistant_delta", text="Reading the config "),
        _tool_event("assistant_delta", text="to find the setting.\nPROGRESS: 20 | scanning"),
    ]

    snap = mgr.live_activity([1, 2, 3, 4])

    assert snap[1]["active_tool"] == "view"
    assert snap[1]["active_tool_count"] == 1
    assert snap[1]["pending_permission"] is None
    # Turn boundary cleared the never-completed tool.
    assert snap[2]["active_tool"] is None
    assert snap[2]["active_tool_count"] == 0
    # Agent not live in-process → empty snapshot, no crash.
    assert snap[3] == {
        "active_tool": None,
        "active_tool_count": 0,
        "pending_permission": None,
        "active_narration": None,
    }
    # In-flight deltas win over the older message; the PROGRESS directive line and
    # trailing prose are dropped, leaving the first meaningful sentence.
    assert snap[4]["active_narration"] == "Reading the config to find the setting."


def test_sanitize_model_downgrades_stale_pin_but_respects_empty_catalog() -> None:
    """A model that the runtime no longer offers is swapped for ``auto`` so the
    turn can start, while ``auto``, a still-valid id, and an empty catalogue
    (runtime momentarily down) are all left untouched."""
    import asyncio

    from precursor.backend.services.agents.manager import AgentManager

    mgr = AgentManager()

    async def _catalog(ids: list[str]):
        return [{"id": i} for i in ids]

    async def _run() -> None:
        # Stale pin with a populated catalogue → falls back to auto.
        mgr.list_models = lambda: _catalog(["auto", "claude-sonnet-5"])  # type: ignore[method-assign]
        assert await mgr._sanitize_model(1, "claude-sonnet-4.5") == "auto"
        # Still-valid pin is preserved.
        assert await mgr._sanitize_model(1, "claude-sonnet-5") == "claude-sonnet-5"
        # ``auto`` and empty selections pass through without a catalogue lookup.
        assert await mgr._sanitize_model(1, "auto") == "auto"
        assert await mgr._sanitize_model(1, "") == ""
        # Empty catalogue (runtime down) must not mask the selection as a change.
        mgr.list_models = lambda: _catalog([])  # type: ignore[method-assign]
        assert await mgr._sanitize_model(1, "claude-sonnet-4.5") == "claude-sonnet-4.5"

    asyncio.run(_run())


# ======================================================================
# Orchestrator: fleet DAG, budgets, retries, blueprints, inbox, metrics
# ======================================================================


async def _set_agents_enabled(enabled: bool) -> None:
    """Flip the persisted agents_enabled flag directly (no manager start)."""
    import json

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AppSetting

    async with SessionLocal() as session:
        row = await session.get(AppSetting, "agents_enabled")
        encoded = json.dumps(enabled)
        if row is None:
            session.add(AppSetting(key="agents_enabled", value=encoded))
        else:
            row.value = encoded
        await session.commit()


async def test_spawn_agent_parked_stays_waiting() -> None:
    """``start=False`` parks an agent in ``waiting`` (not started)."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.routers.agents import _spawn_agent

    await _ensure_schema()
    async with SessionLocal() as session:
        agent = await _spawn_agent(
            session,
            title="Parked",
            task_prompt="wait for a trigger",
            model=None,
            topic_id=None,
            chat_id=None,
            role_id=None,
            autonomy_enabled=False,
            max_steps=8,
            approval_policy=None,
            token_budget=None,
            max_retries=0,
            blueprint_id=None,
            start=False,
        )
        agent_id = agent.id

    async with SessionLocal() as session:
        row = await session.get(AgentSession, agent_id)
        assert row is not None
        assert row.status == "waiting"


async def test_fleet_running_count_counts_busy_agents() -> None:
    """``running_count`` is the concurrency governor: it counts agents that
    currently occupy a slot (running / needs_approval / …)."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.services.agents import fleet

    await _ensure_schema()
    await _make_agent(title="d2", status="running")
    await _make_agent(title="busy", status="needs_approval")

    async with SessionLocal() as session:
        # running + needs_approval both occupy a slot. Other tests share this
        # DB, so assert the two we added are counted, not an exact total.
        assert await fleet.running_count(session) >= 2


async def test_list_agents_serializes_waiting_status() -> None:
    """A parked ``waiting`` agent must not 500 the whole list endpoint.

    Regression: ``waiting`` was added to the DB + frontend for parked agents but
    left out of the ``AgentSessionRead.status`` literal, so a single waiting
    agent raised a Pydantic ``literal_error`` for the *entire* ``GET /api/agents``
    response — hiding every agent behind the empty-state start wizard.
    """
    await _ensure_schema()
    parked = await _make_agent(title="parked", status="waiting")

    with TestClient(create_app()) as client:
        resp = client.get("/api/agents")
    assert resp.status_code == 200
    row = next(a for a in resp.json() if a["id"] == parked)
    assert row["status"] == "waiting"


async def test_clear_artifacts_wipes_previous_run() -> None:
    """Starting a fresh objective run drops artifacts a prior run published."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentArtifact
    from precursor.backend.services.agents.manager import AgentManager

    await _ensure_schema()
    agent_id = await _make_agent(status="completed")
    async with SessionLocal() as session:
        session.add(
            AgentArtifact(agent_id=agent_id, key="stale", kind="text", title="Old", content="gone")
        )
        await session.commit()

    await AgentManager()._clear_artifacts(agent_id)

    async with SessionLocal() as session:
        rows = (
            (await session.execute(select(AgentArtifact).where(AgentArtifact.agent_id == agent_id)))
            .scalars()
            .all()
        )
    assert rows == []


async def test_clear_session_wipes_artifacts() -> None:
    """A `/clear` freshens the blackboard too: clearing an agent's context drops
    the discarded run's published artifacts so stale deliverables don't linger."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentArtifact
    from precursor.backend.services.agents.manager import AgentManager

    await _ensure_schema()
    agent_id = await _make_agent(status="completed", copilot_session_id="sess-clear")
    async with SessionLocal() as session:
        session.add(
            AgentArtifact(
                agent_id=agent_id, key="output", kind="text", title="Draft", content="stale"
            )
        )
        await session.commit()

    await AgentManager().clear_session(agent_id)

    async with SessionLocal() as session:
        rows = (
            (await session.execute(select(AgentArtifact).where(AgentArtifact.agent_id == agent_id)))
            .scalars()
            .all()
        )
    assert rows == []
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentArtifact
    from precursor.backend.services.agents.manager import AgentManager

    await _ensure_schema()
    agent_id = await _make_agent(status="completed")
    mgr = AgentManager()
    art = [{"title": "Result", "content": "the answer"}]
    await mgr._persist_artifacts(agent_id, art, kind="result")
    await mgr._persist_artifacts(agent_id, art, kind="result")  # identical → skipped

    async with SessionLocal() as session:
        rows = (
            (await session.execute(select(AgentArtifact).where(AgentArtifact.agent_id == agent_id)))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].key == "result"
    assert rows[0].kind == "text"


def test_retry_due_at_exponential_backoff() -> None:
    from datetime import UTC, datetime

    from precursor.backend.services.agents.manager import AgentManager

    mgr = AgentManager()
    base = get_settings().agents_retry_backoff_seconds
    now = datetime.now(UTC)
    d0 = (mgr._retry_due_at(0) - now).total_seconds()
    d1 = (mgr._retry_due_at(1) - now).total_seconds()
    d2 = (mgr._retry_due_at(2) - now).total_seconds()
    assert d0 == pytest.approx(base, abs=2)
    assert d1 == pytest.approx(base * 2, abs=2)
    assert d2 == pytest.approx(base * 4, abs=2)


async def test_retry_agent_bumps_count_and_replays(monkeypatch) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.agents.manager import AgentManager

    await _ensure_schema()
    agent_id = await _make_agent(status="failed", max_retries=2, retry_count=0)

    mgr = AgentManager()
    replayed: list[int] = []

    async def fake_restart(aid: int) -> None:
        replayed.append(aid)

    mgr.restart_with_task = fake_restart  # type: ignore[method-assign]
    await mgr.retry_agent(agent_id)

    assert replayed == [agent_id]
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        assert agent.retry_count == 1
        assert agent.next_retry_at is None
        assert agent.error is None

    # Budget exhausted → no further retry.
    replayed.clear()
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        agent.status = "failed"
        agent.retry_count = 2
        await session.commit()
    await mgr.retry_agent(agent_id)
    assert replayed == []


def _get(client, path: str):
    resp = client.get(path)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_metrics_rollup_counts_and_tokens() -> None:
    await _ensure_schema()
    await _make_agent(status="running", total_input_tokens=100, total_output_tokens=50)
    await _make_agent(status="completed", total_input_tokens=10, total_output_tokens=5)
    await _make_agent(status="failed")
    await _make_agent(status="needs_approval")

    with TestClient(create_app()) as client:
        metrics = _get(client, "/api/agents/metrics")
    assert metrics["total"] >= 4
    assert metrics["active"] >= 2  # running + needs_approval
    assert metrics["completed"] >= 1
    assert metrics["failed"] >= 1
    assert metrics["total_input_tokens"] >= 110
    assert metrics["total_output_tokens"] >= 55
    assert metrics["max_concurrent"] == get_settings().agents_max_concurrent


async def test_inbox_classifies_blocked_budget_and_approval() -> None:
    await _ensure_schema()
    # A plain raised-question block.
    await _make_agent(title="asker", status="blocked", blocked_question="Which region?")
    # A budget park: blocked + spend >= budget.
    await _make_agent(
        title="spender",
        status="blocked",
        token_budget=100,
        total_input_tokens=80,
        total_output_tokens=40,
        blocked_question="I've reached my token budget",
    )
    # A permission gate.
    await _make_agent(title="gated", status="needs_approval")

    with TestClient(create_app()) as client:
        inbox = _get(client, "/api/agents/inbox")
    kinds = {item["title"]: item["kind"] for item in inbox}
    assert kinds["asker"] == "blocked"
    assert kinds["spender"] == "budget"
    assert kinds["gated"] == "needs_approval"


async def test_blueprint_crud_lifecycle() -> None:
    await _ensure_schema()
    with TestClient(create_app()) as client:
        created = client.post(
            "/api/agents/blueprints",
            json={
                "name": "Nightly triage",
                "task_prompt": "Triage the queue",
                "autonomy_enabled": True,
                "max_steps": 20,
                "token_budget": 5000,
                "icon": "sparkles",
            },
        )
        assert created.status_code == 201, created.text
        bp = created.json()
        assert bp["name"] == "Nightly triage"
        assert bp["token_budget"] == 5000

        listed = _get(client, "/api/agents/blueprints")
        assert any(b["id"] == bp["id"] for b in listed)

        patched = client.patch(f"/api/agents/blueprints/{bp['id']}", json={"max_steps": 30})
        assert patched.status_code == 200
        assert patched.json()["max_steps"] == 30

        deleted = client.delete(f"/api/agents/blueprints/{bp['id']}")
        assert deleted.status_code == 204
        assert (
            client.patch(f"/api/agents/blueprints/{bp['id']}", json={"max_steps": 1}).status_code
            == 404
        )


async def test_webhook_fires_agent(monkeypatch) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentTrigger
    from precursor.backend.services.agents import runtime
    from precursor.backend.services.agents.manager import AgentManager

    monkeypatch.setattr(runtime, "agents_available", lambda: (True, "ok"))
    fired: list[int] = []

    async def fake_restart(self, aid: int) -> None:
        fired.append(aid)

    monkeypatch.setattr(AgentManager, "restart_with_task", fake_restart)

    await _ensure_schema()
    await _set_agents_enabled(True)
    agent_id = await _make_agent(status="idle")
    async with SessionLocal() as session:
        trig = AgentTrigger(agent_id=agent_id, type="webhook", enabled=True)
        session.add(trig)
        await session.commit()
        await session.refresh(trig)
        token = trig.token

    try:
        with TestClient(create_app()) as client:
            resp = client.post(f"/api/agents/hooks/{token}")
            assert resp.status_code == 200, resp.text
            # An unknown token 404s without leaking existence.
            assert client.post("/api/agents/hooks/nope").status_code == 404
        assert fired == [agent_id]
    finally:
        await _set_agents_enabled(False)


async def test_artifact_endpoints() -> None:
    await _ensure_schema()
    up = await _make_agent(title="up", status="completed")

    with TestClient(create_app()) as client:
        # Publish an artifact by hand.
        art = client.post(
            f"/api/agents/{up}/artifacts",
            json={"title": "Summary", "content": "done", "kind": "markdown"},
        )
        assert art.status_code == 201, art.text
        arts = _get(client, f"/api/agents/{up}/artifacts")
        assert arts[0]["title"] == "Summary"
        assert arts[0]["kind"] == "markdown"


async def test_update_agent_raises_budget_unparks_blocked() -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    await _ensure_schema()
    agent_id = await _make_agent(
        status="blocked",
        token_budget=100,
        total_input_tokens=90,
        total_output_tokens=20,
        blocked_question="budget",
    )
    with TestClient(create_app()) as client:
        resp = client.patch(f"/api/agents/{agent_id}", json={"token_budget": 100000})
        assert resp.status_code == 200, resp.text
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        assert agent.status == "idle"
        assert agent.token_budget == 100000
        assert agent.blocked_question is None


def test_parse_directives_normalizes_artifact_markdown() -> None:
    """ARTIFACT content on one line renders as real Markdown.

    A single directive line can't hold real newlines, so the parser unescapes a
    literal ``\\n`` and breaks a packed sequential numbered list onto separate
    lines — while leaving incidental "2." tokens (versions, prices) untouched.
    """
    from precursor.backend.services.agents.manager import parse_agent_directives

    escaped = parse_agent_directives("ARTIFACT: Next steps | Do these:\\n\\n1. First\\n2. Second")[
        "artifacts"
    ][0]["content"]
    assert escaped == "Do these:\n\n1. First\n2. Second"

    packed = parse_agent_directives("ARTIFACT: Plan | 1. Wire it 2. Test it 3. Ship it")[
        "artifacts"
    ][0]["content"]
    assert packed == "1. Wire it\n2. Test it\n3. Ship it"

    # Non-sequential / incidental numbers stay on one line.
    version = parse_agent_directives("ARTIFACT: Note | Upgrade to v2.0 and pin 3.11 exactly")[
        "artifacts"
    ][0]["content"]
    assert version == "Upgrade to v2.0 and pin 3.11 exactly"


def test_parse_directives_captures_block_artifact() -> None:
    """A multi-line ARTIFACT block is captured whole, not just its heading.

    Models routinely put a substantial deliverable across many real lines and
    only a heading after the inline ``|``. The block form (``ARTIFACT: <title>``
    with no pipe, body lines, then ``END_ARTIFACT``) preserves the full body so
    a research inventory / draft / review lands intact for downstream agents.
    """
    from precursor.backend.services.agents.manager import parse_agent_directives

    text = (
        "Here is the result.\n"
        "ARTIFACT: Release announcement\n"
        "## Agents Level Up\n"
        "\n"
        "- Autonomous missions\n"
        "- Control tower\n"
        "END_ARTIFACT\n"
        "PROGRESS: 90 | published the draft"
    )
    parsed = parse_agent_directives(text)
    art = parsed["artifacts"][0]
    assert art["title"] == "Release announcement"
    assert art["content"] == ("## Agents Level Up\n\n- Autonomous missions\n- Control tower")
    # The trailing PROGRESS line is a directive, not part of the artifact body.
    assert parsed["progress"]["value"] == 90


def test_parse_directives_block_ends_at_next_directive() -> None:
    """A block with no explicit END_ARTIFACT terminates at the next directive."""
    from precursor.backend.services.agents.manager import parse_agent_directives

    text = (
        "ARTIFACT: Inventory\n1. First\n2. Second\nOBJECTIVE_COMPLETE: done gathering the inventory"
    )
    parsed = parse_agent_directives(text)
    assert parsed["artifacts"][0]["content"] == "1. First\n2. Second"
    assert parsed["complete"] == "done gathering the inventory"


def test_parse_directives_strips_trailing_control_line_from_inline() -> None:
    """A control line glued onto inline artifact content is peeled off the tail."""
    from precursor.backend.services.agents.manager import parse_agent_directives

    content = parse_agent_directives(
        "ARTIFACT: Inventory | 1. First\\n2. Second\\nOBJECTIVE_COMPLETE: all done"
    )["artifacts"][0]["content"]
    assert content == "1. First\n2. Second"


def test_parse_directives_ignores_marker_quoted_in_prose() -> None:
    """A directive token narrated mid-sentence must not misfire and block the run.

    An autonomous agent explaining its own protocol ("I emit **NEED_INPUT:** to
    the dashboard when blocked") once tripped the unanchored parser: it matched
    the bolded token mid-prose, captured the trailing ``**`` and surfaced a
    garbled phantom question that halted the workflow. Directives are now only
    recognised at the start of a line.
    """
    from precursor.backend.services.agents.manager import parse_agent_directives

    text = (
        "My draft is complete and ready for the downstream REVIEW agent.\n"
        "I never post **NEED_INPUT:** to your dashboard when blocked."
    )
    assert parse_agent_directives(text) == {}


def test_parse_directives_bolded_label_does_not_leak_emphasis() -> None:
    """A ``**NEED_INPUT:**`` label on its own line yields a clean question.

    The closing ``**`` of the bolded label must be eaten so it never leaks into
    the captured value and unbalances the Markdown the callout renders.
    """
    from precursor.backend.services.agents.manager import parse_agent_directives

    parsed = parse_agent_directives("**NEED_INPUT:** Should I deploy to prod or staging?")
    assert parsed["blocked"] == "Should I deploy to prod or staging?"


# --- Workflow gate loop-back coordinator -----------------------------------
# The workflow coordinator turns a bare 3-step prompt chain (produce → gate →
# record) into a conditional loop: a ``gate`` step votes PASS/FAIL, and on FAIL
# the run re-drives an earlier step until it passes or ``max_loops`` is hit.
# The SDK can't run in CI, so we seed rows and call ``_advance_one`` directly
# with a fake manager that records ``start_task`` hand-offs.


async def _ensure_schema() -> None:
    """Bring the scratch DB to head without booting the whole app.

    The coordinator tests drive ``services/agents/workflow`` directly, so they
    need the schema but not an ASGI lifespan (whose agent-manager boot dominates
    their runtime once earlier tests have enabled agents). ``init_db`` is
    idempotent, so calling it per test is cheap.
    """
    from precursor.backend.db import init_db

    await init_db()


class _FakeWorkflowManager:
    """Captures ``enqueue(start_task(agent_id, extra_context=…))`` hand-offs."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str | None]] = []

    def start_task(self, agent_id: int, extra_context: str | None = None):
        return (agent_id, extra_context)

    def enqueue(self, item) -> None:
        self.calls.append(item)


async def _seed_gate_workflow(
    *,
    gate_summary: str,
    gate_status: str = "idle",
    gate_attempts: int = 0,
    max_loops: int = 3,
) -> tuple[int, list[int]]:
    """Seed produce → gate → record with agents, parked on the gate step.

    Returns ``(workflow_id, [produce_agent, gate_agent, record_agent])``.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.models.workflow import Workflow, WorkflowStep

    async with SessionLocal() as session:
        produce = AgentSession(title="Story", task_prompt="tell a story", status="idle")
        gate = AgentSession(title="Safety", task_prompt="is it safe", status=gate_status)
        gate.result_summary = gate_summary
        record = AgentSession(title="Note", task_prompt="note it", status="idle")
        session.add_all([produce, gate, record])
        await session.commit()
        await session.refresh(produce)
        await session.refresh(gate)
        await session.refresh(record)

        wf = Workflow(name="Story safety", status="running", max_loops=max_loops)
        session.add(wf)
        await session.commit()
        await session.refresh(wf)

        s0 = WorkflowStep(workflow_id=wf.id, position=0, agent_id=produce.id, kind="task")
        s1 = WorkflowStep(
            workflow_id=wf.id,
            position=1,
            agent_id=gate.id,
            kind="gate",
            attempt_count=gate_attempts,
        )
        s2 = WorkflowStep(workflow_id=wf.id, position=2, agent_id=record.id, kind="task")
        session.add_all([s0, s1, s2])
        await session.commit()
        await session.refresh(s1)
        wf.current_step_id = s1.id
        await session.commit()
        return wf.id, [produce.id, gate.id, record.id]


async def test_gate_pass_advances_to_next_step() -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow, WorkflowStep
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, [_produce_id, gate_id, record_id] = await _seed_gate_workflow(
        gate_summary="OBJECTIVE_COMPLETE: PASS: safe for kids",
    )
    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, gate_id, "idle")

    # Advanced forward to the record step, handing it off exactly once.
    assert [c[0] for c in mgr.calls] == [record_id]
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        steps = (
            (await session.execute(select(WorkflowStep).where(WorkflowStep.workflow_id == wf_id)))
            .scalars()
            .all()
        )
        record_step = next(s for s in steps if s.agent_id == record_id)
        assert wf is not None and wf.current_step_id == record_step.id
        assert wf.status == "running"


async def test_gate_fail_loops_back_with_reason() -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow, WorkflowStep
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, [produce_id, gate_id, _record_id] = await _seed_gate_workflow(
        gate_summary="OBJECTIVE_COMPLETE: FAIL: too scary for a child",
    )
    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, gate_id, "idle")

    # Looped back to the produce step, injecting the critique.
    assert len(mgr.calls) == 1
    called_agent, context = mgr.calls[0]
    assert called_agent == produce_id
    assert context is not None and "too scary" in context

    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        steps = (
            (await session.execute(select(WorkflowStep).where(WorkflowStep.workflow_id == wf_id)))
            .scalars()
            .all()
        )
        produce_step = next(s for s in steps if s.agent_id == produce_id)
        gate_step = next(s for s in steps if s.agent_id == gate_id)
        assert wf is not None and wf.current_step_id == produce_step.id
        assert wf.status == "running"
        assert gate_step.attempt_count == 1


async def test_gate_fail_exceeds_max_loops_fails_workflow() -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    # Already at the cap; the next FAIL pushes attempt_count past max_loops.
    wf_id, [_produce_id, gate_id, _record_id] = await _seed_gate_workflow(
        gate_summary="OBJECTIVE_COMPLETE: FAIL: still unsafe",
        gate_attempts=3,
        max_loops=3,
    )
    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, gate_id, "idle")

    # No further hand-off; the workflow gives up.
    assert mgr.calls == []
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None
        assert wf.status == "failed"
        assert wf.current_step_id is None
        assert "still unsafe" in (wf.error or "")


async def test_collect_step_context_prefers_full_assistant_message() -> None:
    """Tier-1: a generative step's full body survives OBJECTIVE_COMPLETE folding.

    ``result_summary`` is folded to the terse directive summary when a turn ends
    with ``OBJECTIVE_COMPLETE:``; the durable event archive still holds the whole
    assistant message, which the hand-off forwards to the next step.
    """
    import json as _json

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentEventRecord, AgentSession
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    async with SessionLocal() as session:
        agent = AgentSession(title="Story", task_prompt="tell a story", status="idle")
        agent.result_summary = "Objective complete."  # the terse fold
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        full = "Once upon a time a brave little fox crossed the river.\nOBJECTIVE_COMPLETE: done"
        session.add(
            AgentEventRecord(
                agent_session_id=agent.id,
                payload=_json.dumps({"kind": "assistant_message", "text": full}),
            )
        )
        await session.commit()
        agent_id = agent.id

    async with SessionLocal() as session:
        ctx = await wf_mod.collect_step_context(session, agent_id)

    assert "brave little fox" in ctx
    # The directive line is stripped from the forwarded body.
    assert "OBJECTIVE_COMPLETE" not in ctx
    # And the terse fold is not what got forwarded.
    assert "Objective complete." not in ctx


async def test_gate_pass_forwards_upstream_content_not_verdict() -> None:
    """Gate transparency: a passed gate forwards the material it validated.

    Regression for the `tell a joke → is it safe? → note the joke` chain, where
    the note step received the gate's terse ``PASS: …`` verdict instead of the
    joke and had nothing to record. The step after a gate must be fed the last
    *non-gate* producer's output, not the gate's own body.
    """
    import json as _json

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentEventRecord
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, [produce_id, gate_id, record_id] = await _seed_gate_workflow(
        gate_summary="OBJECTIVE_COMPLETE: PASS: Safe, harmless tech joke",
    )
    # The producer's full answer lives in the event archive (Tier-1), while its
    # result_summary was folded to a terse directive — mirroring the live bug.
    async with SessionLocal() as session:
        joke = (
            "Why did the database administrator leave the restaurant?\n\n"
            "Because the table had no primary key.\n\n"
            "OBJECTIVE_COMPLETE: Told a short tech joke."
        )
        session.add(
            AgentEventRecord(
                agent_session_id=produce_id,
                payload=_json.dumps({"kind": "assistant_message", "text": joke}),
            )
        )
        await session.commit()

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, gate_id, "idle")

    assert [c[0] for c in mgr.calls] == [record_id]
    context = mgr.calls[0][1] or ""
    # The note step sees the actual joke…
    assert "primary key" in context
    # …and NOT the gate's bare verdict.
    assert "Safe, harmless tech joke" not in context


async def test_task_step_context_forbids_clarification() -> None:
    """A task step's kickoff forces autonomous completion — never NEED_INPUT.

    A workflow runs unattended, so a step that asks the (absent) human to clarify
    just wedges the pipeline (the coordinator parks it ``blocked`` and pauses).
    Every non-gate step must therefore carry the "act autonomously, don't ask"
    directive; a gate step (which has its own PASS/FAIL contract) must not.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import WorkflowStep
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    async with SessionLocal() as session:
        task_step = WorkflowStep(workflow_id=0, position=0, agent_id=1, kind="task")
        gate_step = WorkflowStep(workflow_id=0, position=0, agent_id=2, kind="gate")

        task_ctx = await wf_mod._build_context(session, task_step, None)
        gate_ctx = await wf_mod._build_context(session, gate_step, None)

    # A task step — even a first one with no upstream input — is told to complete
    # autonomously and explicitly forbidden from emitting NEED_INPUT.
    assert task_ctx is not None
    assert "NEED_INPUT" in task_ctx
    assert "autonomously" in task_ctx.lower()
    # The gate keeps only its own PASS/FAIL contract, not the task directive.
    assert gate_ctx is not None
    assert "QUALITY GATE" in gate_ctx
    assert "never emit NEED_INPUT" not in gate_ctx


async def test_gate_result_is_cleaned_of_directive_tokens() -> None:
    """A passed gate stores a plain verdict and leaves no deliverable artifact.

    A gate judges; it must not surface its raw ``PASS:``/``OBJECTIVE_COMPLETE:``
    control tokens as the step's displayed result, nor drop that verdict onto the
    shared blackboard as a "Result" artifact (a gate produces nothing to inherit).
    """
    import json as _json

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentArtifact, AgentEventRecord, AgentSession
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, [_produce_id, gate_id, _record_id] = await _seed_gate_workflow(
        gate_summary="PASS: harmless family-friendly joke",
    )
    # Seed the raw archived verdict + the auto-captured verdict artifact the
    # manager would have written, mirroring the live leak.
    async with SessionLocal() as session:
        session.add(
            AgentEventRecord(
                agent_session_id=gate_id,
                payload=_json.dumps(
                    {
                        "kind": "assistant_message",
                        "text": "OBJECTIVE_COMPLETE: PASS: harmless family-friendly joke",
                    }
                ),
            )
        )
        session.add(
            AgentArtifact(
                agent_id=gate_id,
                key="result",
                kind="result",
                title="Result",
                content="PASS: harmless family-friendly joke",
            )
        )
        await session.commit()

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, gate_id, "idle")

    async with SessionLocal() as session:
        gate = await session.get(AgentSession, gate_id)
        assert gate is not None
        # The stored result is a plain human verdict — no directive token.
        assert "OBJECTIVE_COMPLETE" not in (gate.result_summary or "")
        assert not (gate.result_summary or "").startswith("PASS")
        assert (gate.result_summary or "").startswith("Passed")
        assert "harmless family-friendly joke" in (gate.result_summary or "")
        # The gate leaves no deliverable artifact on the board.
        arts = (
            (await session.execute(select(AgentArtifact).where(AgentArtifact.agent_id == gate_id)))
            .scalars()
            .all()
        )
        assert arts == []


def test_strip_control_directives_scrubs_tokens_keeps_prose() -> None:
    from precursor.backend.services.agents.manager import strip_control_directives

    raw = (
        "Here is the finished draft.\n"
        "OBJECTIVE_COMPLETE: Wrote the announcement.\n"
        "ARTIFACT: Draft | the body\n"
        "PROGRESS: 100 | done"
    )
    cleaned = strip_control_directives(raw)
    assert cleaned == "Here is the finished draft."
    assert "OBJECTIVE_COMPLETE" not in cleaned
    assert "ARTIFACT" not in cleaned
    assert "PROGRESS" not in cleaned


async def test_collect_prior_artifacts_gathers_earlier_steps() -> None:
    """Blackboard: a step sees the artifacts of *every* earlier producer.

    The drafter's full body is the immediate hand-off, but a later reviewer
    still needs the research inventory published two steps back. Earlier steps
    contribute their durable artifacts as reference material, labelled by step
    and oldest-first; steps that published nothing are skipped.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentArtifact, AgentSession
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    async with SessionLocal() as session:
        research = AgentSession(title="Research", task_prompt="gather", status="idle")
        empty = AgentSession(title="Empty", task_prompt="noop", status="idle")
        session.add_all([research, empty])
        await session.commit()
        await session.refresh(research)
        await session.refresh(empty)
        session.add(
            AgentArtifact(
                agent_id=research.id,
                kind="text",
                title="Inventory",
                content="v1.2.0 shipped with the new parser",
            )
        )
        await session.commit()
        research_id, empty_id = research.id, empty.id

    async with SessionLocal() as session:
        digest = await wf_mod.collect_prior_artifacts(session, [research_id, empty_id])

    # The earlier producer's artifact is surfaced, labelled by its step title.
    assert "v1.2.0 shipped with the new parser" in digest
    assert "Research" in digest
    assert "Inventory" in digest
    # The step that published nothing is skipped, not shown as an empty section.
    assert "Empty" not in digest


def test_earlier_content_agent_ids_skips_gates_and_immediate() -> None:
    """Earlier-producer collection stops before the immediate producer and skips gates."""
    from precursor.backend.models.workflow import WorkflowStep
    from precursor.backend.services.agents import workflow as wf_mod

    steps = [
        WorkflowStep(agent_id=10, kind="task", position=0),
        WorkflowStep(agent_id=20, kind="gate", position=1),
        WorkflowStep(agent_id=30, kind="task", position=2),
        WorkflowStep(agent_id=40, kind="task", position=3),
    ]
    # Immediate producer is at index 3 (agent 40) — collect everything before it,
    # excluding the gate at index 1.
    assert wf_mod._earlier_content_agent_ids(steps, 3) == [10, 30]
    # No prior producer → empty trail.
    assert wf_mod._earlier_content_agent_ids(steps, None) == []
    assert wf_mod._earlier_content_agent_ids(steps, 0) == []


async def test_completed_result_shows_deliverable_not_objective_reason() -> None:
    """A completed step displays its *deliverable*, not the terse OBJECTIVE_COMPLETE reason.

    Regression for the workflow "Joker" step: the model wrote the joke as prose
    then ended with ``OBJECTIVE_COMPLETE: Told the user a joke``. The displayed
    result and the auto-captured "Result" artifact showed the meta-reason instead
    of the joke — even though the downstream hand-off (which reads the archived
    message body) received the real joke. The result must prefer the prose body.
    """
    from sqlalchemy import select

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentArtifact, AgentSession
    from precursor.backend.services.agents.manager import AgentManager, _LiveSession

    await _ensure_schema()

    agent_id = await _make_agent(task_prompt="Tell me a joke", autonomy_enabled=True)
    joke = "Why did the developer go broke? He used up all his cache."

    mgr = AgentManager()
    live = _LiveSession(sdk_session=None)
    live.pending_answer = (
        f"{joke}\nOBJECTIVE_COMPLETE: Told the user a joke as requested. No further work needed."
    )
    mgr._live[agent_id] = live

    patch: dict[str, object] = {}
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        await mgr._on_idle(agent, patch)

    # The displayed result is the joke, not the OBJECTIVE_COMPLETE meta-reason.
    assert patch["status"] == "completed"
    assert patch["result_summary"] == joke
    assert "OBJECTIVE_COMPLETE" not in str(patch["result_summary"])
    assert "Told the user a joke as requested" not in str(patch["result_summary"])

    # The auto-captured Result artifact carries the deliverable too.
    async with SessionLocal() as session:
        arts = (
            (await session.execute(select(AgentArtifact).where(AgentArtifact.agent_id == agent_id)))
            .scalars()
            .all()
        )
    assert any(a.content == joke for a in arts)


async def test_completed_result_falls_back_to_reason_when_body_is_directive_only() -> None:
    """When the final turn is a bare OBJECTIVE_COMPLETE (no prose), the reason stands in.

    A multi-step mission whose last message is only the completion directive has
    no deliverable prose to show, so the terse reason remains the result rather
    than an empty summary.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.agents.manager import AgentManager, _LiveSession

    await _ensure_schema()

    agent_id = await _make_agent(task_prompt="Do the mission", autonomy_enabled=True)

    mgr = AgentManager()
    live = _LiveSession(sdk_session=None)
    live.pending_answer = "OBJECTIVE_COMPLETE: Completed the three-stage mission."
    mgr._live[agent_id] = live

    patch: dict[str, object] = {}
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        await mgr._on_idle(agent, patch)

    assert patch["status"] == "completed"
    assert patch["result_summary"] == "Completed the three-stage mission."


# --- Run-history trace recording -------------------------------------------
# The coordinator persists a durable trace as it drives a run: one WorkflowRun
# per execution, one WorkflowRunStep per step attempt (inputs + outputs + gate
# verdicts). These exercise ``_advance_one`` with an open run and assert the
# trace rows it leaves behind.


async def _open_run_for(workflow_id: int, *, trigger: str = "manual") -> int:
    """Attach a fresh running WorkflowRun to a seeded workflow and point it there.

    Also seeds an open (running) trace for the workflow's *current* step, mirroring
    the row ``_launch_step`` would have written when the coordinator drove into it —
    so a subsequent ``_advance_one`` has an open trace to finalize.
    """
    from datetime import UTC, datetime

    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import (
        Workflow,
        WorkflowRun,
        WorkflowRunStep,
        WorkflowStep,
    )

    async with SessionLocal() as session:
        wf = await session.get(Workflow, workflow_id)
        assert wf is not None
        run = WorkflowRun(
            workflow_id=workflow_id,
            run_number=1,
            status="running",
            trigger=trigger,
            started_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()
        wf.current_run_id = run.id
        current = await session.get(WorkflowStep, wf.current_step_id)
        if current is not None and current.agent_id is not None:
            session.add(
                WorkflowRunStep(
                    run_id=run.id,
                    position=current.position,
                    kind=current.kind,
                    label=current.name,
                    agent_id=current.agent_id,
                    attempt=1,
                    status="running",
                    input_context=None,
                    started_at=datetime.now(UTC),
                )
            )
        await session.commit()
        return run.id


async def _run_steps(run_id: int) -> list:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import WorkflowRunStep

    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(WorkflowRunStep)
                    .where(WorkflowRunStep.run_id == run_id)
                    .order_by(WorkflowRunStep.id)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


async def test_run_trace_gate_pass_records_verdict_and_completes_run() -> None:
    """A passing gate then a final step leaves a completed run whose traces carry
    the gate verdict and the pipeline's deliverable."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import WorkflowRun
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, [_produce_id, gate_id, record_id] = await _seed_gate_workflow(
        gate_summary="OBJECTIVE_COMPLETE: PASS: safe for kids",
    )
    run_id = await _open_run_for(wf_id)

    mgr = _FakeWorkflowManager()
    # Gate passes → advances to record step (records a trace for it).
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, gate_id, "idle")
    # Record step finishes → pipeline completes.
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, record_id, "idle")

    steps = await _run_steps(run_id)
    by_agent = {s.agent_id: s for s in steps}
    # The gate trace carries a PASS verdict and is marked passed.
    gate_trace = by_agent[gate_id]
    assert gate_trace.status == "passed"
    assert gate_trace.gate_verdict == "PASS"
    # The record step was traced and completed.
    record_trace = by_agent[record_id]
    assert record_trace.status == "completed"
    assert record_trace.finished_at is not None
    # The run itself is completed.
    async with SessionLocal() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None
        assert run.status == "completed"
        assert run.finished_at is not None


async def test_run_trace_gate_fail_records_attempt_and_loops_back() -> None:
    """A failing gate records a FAIL verdict trace and appends a *new* attempt
    trace for the step it loops back to, capturing the injected critique."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, [produce_id, gate_id, _record_id] = await _seed_gate_workflow(
        gate_summary="OBJECTIVE_COMPLETE: FAIL: too scary for a child",
    )
    run_id = await _open_run_for(wf_id)

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, gate_id, "idle")

    steps = await _run_steps(run_id)
    # The gate trace is a failed FAIL verdict.
    gate_trace = next(s for s in steps if s.agent_id == gate_id)
    assert gate_trace.status == "failed"
    assert gate_trace.gate_verdict == "FAIL"
    # A fresh trace for the looped-back produce step was appended, running, with
    # the critique injected as its input.
    produce_trace = next(s for s in steps if s.agent_id == produce_id)
    assert produce_trace.status == "running"
    assert produce_trace.attempt == 1
    assert produce_trace.input_context is not None
    assert "too scary" in produce_trace.input_context


async def test_run_trace_failed_step_marks_run_failed() -> None:
    """A hard step failure finalizes both the step trace and the run as failed."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import WorkflowRun
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, [_produce_id, gate_id, _record_id] = await _seed_gate_workflow(
        gate_summary="whatever",
    )
    run_id = await _open_run_for(wf_id)

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, gate_id, "failed")

    steps = await _run_steps(run_id)
    gate_trace = next(s for s in steps if s.agent_id == gate_id)
    assert gate_trace.status == "failed"
    assert gate_trace.finished_at is not None
    async with SessionLocal() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None
        assert run.status == "failed"


async def test_workflow_step_finalize_clears_agent_unread_badge() -> None:
    """A turn an agent runs *as a workflow step* must not surface as unread in the
    Agents section — the workflow is the coordinator, not an autonomous run.

    Seed an assistant reply that would otherwise be unread (it lands after the
    agent's ``last_read_at``), then drive the step to completion and assert the
    badge is cleared because ``_finalize_run_step`` advances ``last_read_at`` past
    the step's events. A plain autonomous reply after that still counts.
    """
    from datetime import UTC, datetime, timedelta

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentEventRecord, AgentSession
    from precursor.backend.schemas.agent import AgentEvent
    from precursor.backend.services.agents import workflow as wf_mod

    def reply(text: str) -> str:
        return AgentEvent(kind="assistant_message", text=text).model_dump_json()

    wf_id, [_produce_id, gate_id, _record_id] = await _seed_gate_workflow(
        gate_summary="OBJECTIVE_COMPLETE: PASS: safe for kids",
    )
    await _open_run_for(wf_id)  # seeds the open trace the gate step finalizes

    # The gate agent was "opened" a minute ago, then produced a reply *as part of
    # the workflow step*. Pre-finalize that reply is unread (lands after read).
    async with SessionLocal() as session:
        gate = await session.get(AgentSession, gate_id)
        assert gate is not None
        gate.last_read_at = datetime.now(UTC) - timedelta(minutes=1)
        session.add(AgentEventRecord(agent_session_id=gate_id, payload=reply("verdict")))
        await session.commit()

    with TestClient(create_app()) as client:
        row = next(a for a in client.get("/api/agents").json() if a["id"] == gate_id)
        assert row["unread_count"] == 1  # unread before the step finalizes

        mgr = _FakeWorkflowManager()
        async with SessionLocal() as session:
            wf = await wf_mod._load_workflow(session, wf_id)
            assert wf is not None
            await wf_mod._advance_one(session, mgr, wf, gate_id, "idle")

        # Finalizing the gate step marked its workflow-produced reply as read.
        row = next(a for a in client.get("/api/agents").json() if a["id"] == gate_id)
        assert row["unread_count"] == 0

        # A genuinely autonomous reply *after* the step still surfaces as unread.
        async with SessionLocal() as session:
            session.add(AgentEventRecord(agent_session_id=gate_id, payload=reply("ping")))
            await session.commit()
        row = next(a for a in client.get("/api/agents").json() if a["id"] == gate_id)
        assert row["unread_count"] == 1


# --- Per-run brief -----------------------------------------------------------
# A workflow definition is reusable and generic; the *run* carries the subject.
# ``start_workflow(run_input=…)`` stores the brief on the run row and prepends it
# to every step's kickoff preamble — including steps reached many advances later
# and gate loop-backs — so the whole pipeline shares the human's intent. Omitting
# it must leave behaviour exactly as before (fully autonomous).


async def test_run_brief_is_stored_and_fed_to_the_first_step() -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow, WorkflowRun
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, [produce_id, _gate_id, _record_id] = await _seed_gate_workflow(
        gate_summary="OBJECTIVE_COMPLETE: PASS: fine",
    )
    # Park it so start_workflow will drive from the first step.
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None
        wf.status = "idle"
        wf.current_step_id = None
        await session.commit()

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        await wf_mod.start_workflow(
            session, mgr, wf_id, run_input="Analyse /data/q3.csv, EMEA only."
        )

    # The first step was handed the brief as part of its kickoff context.
    assert len(mgr.calls) == 1
    agent_id, context = mgr.calls[0]
    assert agent_id == produce_id
    assert context is not None
    assert "Run brief from the human" in context
    assert "/data/q3.csv" in context

    # And it's durable on the run row, so the trace can show it later.
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None and wf.current_run_id is not None
        run = await session.get(WorkflowRun, wf.current_run_id)
        assert run is not None
        assert run.input == "Analyse /data/q3.csv, EMEA only."


async def test_run_without_brief_stays_autonomous() -> None:
    """No brief → no brief section, and the run row records none."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow, WorkflowRun
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, _agents = await _seed_gate_workflow(gate_summary="OBJECTIVE_COMPLETE: PASS: fine")
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None
        wf.status = "idle"
        wf.current_step_id = None
        await session.commit()

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        await wf_mod.start_workflow(session, mgr, wf_id)

    _agent_id, context = mgr.calls[0]
    assert context is None or "Run brief from the human" not in context
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None and wf.current_run_id is not None
        run = await session.get(WorkflowRun, wf.current_run_id)
        assert run is not None
        assert run.input is None


async def test_run_brief_reaches_later_steps_and_loop_backs() -> None:
    """The brief is re-read from the run for every subsequent hand-off."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.models.workflow import Workflow, WorkflowRun
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, [produce_id, gate_id, record_id] = await _seed_gate_workflow(
        gate_summary="OBJECTIVE_COMPLETE: PASS: safe",
    )
    run_id = await _open_run_for(wf_id)
    async with SessionLocal() as session:
        run = await session.get(WorkflowRun, run_id)
        assert run is not None
        run.input = "The subject is the Q3 sales file."
        await session.commit()

    # A forward advance (gate passes → record step) carries the brief.
    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, gate_id, "idle")
    assert mgr.calls[0][0] == record_id
    assert "Q3 sales file" in (mgr.calls[0][1] or "")

    # A gate loop-back re-drives an earlier step — still briefed.
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None
        steps = sorted(wf.steps, key=lambda s: s.position) if wf.steps else []
        gate_step = next(s for s in steps if s.agent_id == gate_id)
        wf.current_step_id = gate_step.id
        wf.status = "running"
        gate_agent = await session.get(AgentSession, gate_id)
        assert gate_agent is not None
        gate_agent.result_summary = "OBJECTIVE_COMPLETE: FAIL: numbers look wrong"
        await session.commit()

    mgr2 = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr2, wf, gate_id, "idle")
    assert mgr2.calls[0][0] == produce_id
    looped_context = mgr2.calls[0][1] or ""
    assert "Q3 sales file" in looped_context
    assert "numbers look wrong" in looped_context


async def test_run_endpoint_accepts_optional_brief_body() -> None:
    """POST /run takes an optional body; both shapes start a run."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow, WorkflowRun

    with TestClient(create_app()) as client:
        await _set_agents_enabled(True)
        wf_id, _agents = await _seed_gate_workflow(gate_summary="OBJECTIVE_COMPLETE: PASS: ok")
        async with SessionLocal() as session:
            wf = await session.get(Workflow, wf_id)
            assert wf is not None
            wf.status = "idle"
            wf.current_step_id = None
            await session.commit()

        resp = client.post(f"/api/workflows/{wf_id}/run", json={"input": "  Look at report.pdf  "})
        assert resp.status_code == 200

        async with SessionLocal() as session:
            wf = await session.get(Workflow, wf_id)
            assert wf is not None and wf.current_run_id is not None
            run = await session.get(WorkflowRun, wf.current_run_id)
            assert run is not None
            assert run.input == "Look at report.pdf"  # trimmed

        # A bodyless call still works (the legacy "just run it" path).
        async with SessionLocal() as session:
            wf = await session.get(Workflow, wf_id)
            assert wf is not None
            wf.status = "idle"
            await session.commit()
        assert client.post(f"/api/workflows/{wf_id}/run").status_code == 200


async def test_webhook_body_becomes_the_run_brief() -> None:
    """A webhook payload points the pipeline at a subject.

    Explicit ``{"input": …}`` wins; any other JSON is handed over verbatim so a
    third-party hook's payload is still readable by the agents. A bodyless fire
    must still start the run (a webhook never fails over payload shape).
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow, WorkflowRun

    async def _brief_of(wf_id: int) -> str | None:
        async with SessionLocal() as session:
            wf = await session.get(Workflow, wf_id)
            assert wf is not None and wf.current_run_id is not None
            run = await session.get(WorkflowRun, wf.current_run_id)
            assert run is not None
            return run.input

    async def _park(wf_id: int) -> None:
        async with SessionLocal() as session:
            wf = await session.get(Workflow, wf_id)
            assert wf is not None
            wf.status = "idle"
            wf.current_step_id = None
            await session.commit()

    with TestClient(create_app()) as client:
        await _set_agents_enabled(True)
        wf_id, _agents = await _seed_gate_workflow(gate_summary="OBJECTIVE_COMPLETE: PASS: ok")
        token = client.post(f"/api/workflows/{wf_id}/webhook").json()["webhook_token"]

        await _park(wf_id)
        assert (
            client.post(f"/api/workflows/hooks/{token}", json={"input": "Check inbox"}).status_code
            == 202
        )
        assert await _brief_of(wf_id) == "Check inbox"

        # An arbitrary JSON payload is forwarded verbatim.
        await _park(wf_id)
        assert (
            client.post(f"/api/workflows/hooks/{token}", json={"file": "a.csv"}).status_code == 202
        )
        brief = await _brief_of(wf_id)
        assert brief is not None and "a.csv" in brief

        # No body at all — still fires, no brief.
        await _park(wf_id)
        assert client.post(f"/api/workflows/hooks/{token}").status_code == 202
        assert await _brief_of(wf_id) is None


# --- Step policies: instructions, approval, failure handling, cost, watchdog ---
# These five reshape how a run is driven, so they're exercised through the
# coordinator directly (the SDK can't run in CI) with the fake manager capturing
# hand-offs.


async def _seed_linear_workflow(
    *,
    kinds: list[str],
    step_kwargs: list[dict] | None = None,
    **workflow_kwargs,
) -> tuple[int, list[int | None]]:
    """Seed a workflow of ``kinds`` parked on step 0.

    Returns ``(workflow_id, [agent_id_or_None_per_step])``. ``approval`` steps get
    no agent, mirroring how the router stores them.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.models.workflow import Workflow, WorkflowStep

    async with SessionLocal() as session:
        wf = Workflow(name="Policy pipeline", status="running", **workflow_kwargs)
        session.add(wf)
        await session.commit()
        await session.refresh(wf)

        agent_ids: list[int | None] = []
        for pos, kind in enumerate(kinds):
            agent_id: int | None = None
            if kind != "approval":
                agent = AgentSession(title=f"Step {pos}", task_prompt="do it", status="idle")
                session.add(agent)
                await session.commit()
                await session.refresh(agent)
                agent_id = agent.id
            agent_ids.append(agent_id)
            extra = (step_kwargs or [{}] * len(kinds))[pos] or {}
            session.add(
                WorkflowStep(workflow_id=wf.id, position=pos, agent_id=agent_id, kind=kind, **extra)
            )
        await session.commit()

        first = (
            await session.execute(
                select(WorkflowStep).where(
                    WorkflowStep.workflow_id == wf.id, WorkflowStep.position == 0
                )
            )
        ).scalar_one()
        wf.current_step_id = first.id
        await session.commit()
        return wf.id, agent_ids


async def test_step_instructions_are_layered_onto_the_agent() -> None:
    """A step's own mandate reaches its agent, so one agent is reusable."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(
        kinds=["task", "task"],
        step_kwargs=[{}, {"instructions": "Answer in exactly three bullets."}],
    )
    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "idle")

    assert mgr.calls[0][0] == agents[1]
    context = mgr.calls[0][1] or ""
    assert "Your specific instructions for this step" in context
    assert "exactly three bullets" in context


async def test_approval_step_parks_the_run_then_approve_continues() -> None:
    """An approval checkpoint runs no agent: it waits for a human, then resumes."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow, WorkflowRun
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task", "approval", "task"])
    run_id = await _open_run_for(wf_id)

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "idle")

    # Parked on the human, nothing handed off.
    assert mgr.calls == []
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None
        assert wf.status == "awaiting_approval"
        run = await session.get(WorkflowRun, run_id)
        assert run is not None and run.status == "awaiting_approval"
    steps = await _run_steps(run_id)
    approval_trace = next(s for s in steps if s.kind == "approval")
    assert approval_trace.status == "awaiting_approval"
    assert approval_trace.agent_id is None

    # Approving releases the pipeline to the final step.
    mgr2 = _FakeWorkflowManager()
    async with SessionLocal() as session:
        await wf_mod.approve_step(session, mgr2, wf_id, note="Looks good")
    assert [c[0] for c in mgr2.calls] == [agents[2]]
    steps = await _run_steps(run_id)
    approval_trace = next(s for s in steps if s.kind == "approval")
    assert approval_trace.status == "passed"
    assert approval_trace.output_summary == "Looks good"


async def test_approval_reject_loops_back_with_feedback() -> None:
    """Rejecting sends the work back to the producer with the reviewer's notes."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task", "approval", "task"])
    await _open_run_for(wf_id)

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "idle")

    mgr2 = _FakeWorkflowManager()
    async with SessionLocal() as session:
        await wf_mod.reject_step(session, mgr2, wf_id, feedback="Tone is far too casual")

    # Looped back to the producer, carrying the human feedback.
    assert [c[0] for c in mgr2.calls] == [agents[0]]
    context = mgr2.calls[0][1] or ""
    assert "A human reviewed the work and sent it back" in context
    assert "too casual" in context
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None and wf.status == "running"


async def test_step_failure_policy_retry_then_gives_up() -> None:
    """on_error=retry re-drives the same step, then stops once the budget is out."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(
        kinds=["task", "task"],
        step_kwargs=[{"on_error": "retry", "max_retries": 1}, {}],
    )
    await _open_run_for(wf_id)

    # First failure → retried (same step handed off again).
    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "failed")
    assert [c[0] for c in mgr.calls] == [agents[0]]
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None and wf.status == "running"

    # Second failure → budget spent, run fails.
    mgr2 = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr2, wf, agents[0], "failed")
    assert mgr2.calls == []
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None and wf.status == "failed"


async def test_step_failure_policy_continue_skips_onward() -> None:
    """on_error=continue records the failure but keeps the pipeline moving."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(
        kinds=["task", "task"],
        step_kwargs=[{"on_error": "continue"}, {}],
    )
    run_id = await _open_run_for(wf_id)

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "failed")

    assert [c[0] for c in mgr.calls] == [agents[1]]
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None and wf.status == "running"
    steps = await _run_steps(run_id)
    assert next(s for s in steps if s.agent_id == agents[0]).status == "failed"


async def test_step_failure_policy_fail_is_the_default() -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task", "task"])
    await _open_run_for(wf_id)

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "failed")
    assert mgr.calls == []
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None and wf.status == "failed"


async def test_token_spend_is_recorded_per_attempt_and_rolled_up() -> None:
    """Each attempt records its own delta; the run accumulates the total."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task", "task"])
    run_id = await _open_run_for(wf_id)

    # The open trace was seeded without a baseline; set one, then "spend".
    async with SessionLocal() as session:
        trace = (
            await session.execute(
                select(WorkflowRunStep).where(
                    WorkflowRunStep.run_id == run_id, WorkflowRunStep.agent_id == agents[0]
                )
            )
        ).scalar_one()
        trace.token_baseline_in = 100
        trace.token_baseline_out = 10
        agent = await session.get(AgentSession, agents[0])
        assert agent is not None
        agent.total_input_tokens = 350
        agent.total_output_tokens = 60
        await session.commit()

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "idle")

    steps = await _run_steps(run_id)
    first = next(s for s in steps if s.agent_id == agents[0])
    assert first.input_tokens == 250  # 350 - 100
    assert first.output_tokens == 50  # 60 - 10
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None and wf.current_run_id is not None
        run = await session.get(WorkflowRun, wf.current_run_id)
        assert run is not None
        assert run.total_input_tokens == 250
        assert run.total_output_tokens == 50


async def test_watchdog_unsticks_a_stalled_step() -> None:
    """A step running past step_timeout_seconds is stopped and put through policy."""
    from datetime import UTC, datetime, timedelta

    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import WorkflowRunStep
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(
        kinds=["task", "task"],
        step_kwargs=[{"on_error": "continue"}, {}],
        step_timeout_seconds=60,
    )
    run_id = await _open_run_for(wf_id)

    # Backdate the open trace well past the timeout.
    async with SessionLocal() as session:
        trace = (
            await session.execute(
                select(WorkflowRunStep).where(
                    WorkflowRunStep.run_id == run_id, WorkflowRunStep.agent_id == agents[0]
                )
            )
        ).scalar_one()
        trace.started_at = datetime.now(UTC) - timedelta(minutes=30)
        await session.commit()

    mgr = _FakeWorkflowManager()
    mgr.cancel = lambda agent_id: ("cancel", agent_id)  # type: ignore[method-assign]
    async with SessionLocal() as session:
        swept = await wf_mod.sweep_stalled_steps(session, mgr)
    assert swept == 1

    # The stuck agent was cancelled and the policy carried the run onward.
    assert ("cancel", agents[0]) in mgr.calls
    assert agents[1] in [c[0] for c in mgr.calls if isinstance(c[0], int)]
    steps = await _run_steps(run_id)
    stalled = next(s for s in steps if s.agent_id == agents[0])
    assert stalled.status == "failed"
    assert "watchdog" in (stalled.output_summary or "")


async def test_watchdog_ignores_workflows_without_a_timeout() -> None:
    from datetime import UTC, datetime, timedelta

    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import WorkflowRunStep
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task", "task"])
    run_id = await _open_run_for(wf_id)
    async with SessionLocal() as session:
        trace = (
            await session.execute(
                select(WorkflowRunStep).where(
                    WorkflowRunStep.run_id == run_id, WorkflowRunStep.agent_id == agents[0]
                )
            )
        ).scalar_one()
        trace.started_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        assert await wf_mod.sweep_stalled_steps(session, mgr) == 0
    assert mgr.calls == []


async def test_approval_reject_policy_stop_ends_the_run() -> None:
    """on_reject=stop means "don't do this at all" — the run ends, not fails."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow, WorkflowRun
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(
        kinds=["task", "approval", "task"],
        step_kwargs=[{}, {"on_reject": "stop"}, {}],
    )
    run_id = await _open_run_for(wf_id)

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "idle")

    mgr2 = _FakeWorkflowManager()
    async with SessionLocal() as session:
        await wf_mod.reject_step(session, mgr2, wf_id, feedback="Do not send this")

    # Nothing else ran; the run is cancelled (a decision, not a failure).
    assert mgr2.calls == []
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None
        assert wf.status == "cancelled"
        assert wf.current_step_id is None
        assert "Do not send this" in (wf.result_summary or "")
        run = await session.get(WorkflowRun, run_id)
        assert run is not None
        assert run.status == "cancelled"
        assert run.finished_at is not None


async def test_approval_reject_policy_skip_carries_on() -> None:
    """on_reject=skip drops the rejected work but keeps the pipeline moving."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(
        kinds=["task", "approval", "task"],
        step_kwargs=[{}, {"on_reject": "skip"}, {}],
    )
    run_id = await _open_run_for(wf_id)

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "idle")

    mgr2 = _FakeWorkflowManager()
    async with SessionLocal() as session:
        await wf_mod.reject_step(session, mgr2, wf_id, feedback="Not needed this time")

    assert [c[0] for c in mgr2.calls] == [agents[2]]
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None and wf.status == "running"
    steps = await _run_steps(run_id)
    assert next(s for s in steps if s.kind == "approval").status == "skipped"


async def test_approval_reject_action_overrides_the_step_policy() -> None:
    """A reviewer can stop a run even when the checkpoint defaults to rework."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task", "approval", "task"])
    await _open_run_for(wf_id)

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "idle")

    mgr2 = _FakeWorkflowManager()
    async with SessionLocal() as session:
        await wf_mod.reject_step(session, mgr2, wf_id, feedback="Abort", action="stop")

    assert mgr2.calls == []
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None and wf.status == "cancelled"


async def test_approval_reject_defaults_to_rework() -> None:
    """Unset policy keeps the original behaviour: loop back and redo."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task", "approval", "task"])
    await _open_run_for(wf_id)

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "idle")

    mgr2 = _FakeWorkflowManager()
    async with SessionLocal() as session:
        await wf_mod.reject_step(session, mgr2, wf_id, feedback="Try again")

    assert [c[0] for c in mgr2.calls] == [agents[0]]
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None and wf.status == "running"


async def test_approval_note_is_forwarded_to_later_steps() -> None:
    """A reviewer's approval note is a *directive* and must reach later steps.

    Regression: approval steps are transparent to the data flow (they publish no
    content), so skipping them also dropped the note — "translate it into French
    before sending" never reached the sender, which shipped the original.
    The content still comes from the last real producer; the note rides alongside.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task", "approval", "task"])
    await _open_run_for(wf_id)

    # Give the producer a deliverable so we can prove content still flows too.
    async with SessionLocal() as session:
        producer = await session.get(AgentSession, agents[0])
        assert producer is not None
        producer.result_summary = "Perche i programmatori preferiscono la modalita scura?"
        await session.commit()

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "idle")

    mgr2 = _FakeWorkflowManager()
    async with SessionLocal() as session:
        await wf_mod.approve_step(session, mgr2, wf_id, note="Translate in French before sending")

    assert [c[0] for c in mgr2.calls] == [agents[2]]
    context = mgr2.calls[0][1] or ""
    assert "Instructions added by the reviewer" in context
    assert "Translate in French" in context
    # …and the actual material still came through from the producer.
    assert "modalita scura" in context


async def test_bare_approval_adds_no_reviewer_directive() -> None:
    """Approving without a note must not inject a bogus 'Approved.' instruction."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task", "approval", "task"])
    await _open_run_for(wf_id)

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "idle")

    mgr2 = _FakeWorkflowManager()
    async with SessionLocal() as session:
        await wf_mod.approve_step(session, mgr2, wf_id)

    context = mgr2.calls[0][1] or ""
    assert "Instructions added by the reviewer" not in context
    assert "Approved." not in context


# --- Context sourcing, capability overrides and the workflow role -------------


async def test_context_mode_selected_narrows_the_inherited_material() -> None:
    """A step can name exactly which earlier steps it inherits from."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(
        kinds=["task", "task", "task"],
        step_kwargs=[{}, {}, {"context_mode": "selected", "context_sources": "0"}],
    )
    async with SessionLocal() as session:
        for idx, marker in ((0, "ALPHA-FROM-STEP-ONE"), (1, "BETA-FROM-STEP-TWO")):
            agent = await session.get(AgentSession, agents[idx])
            assert agent is not None
            agent.result_summary = marker
        await session.commit()

    # Drive step 2 (position 1) to done so the run advances into position 2.
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        steps = sorted(wf.steps, key=lambda s: s.position)
        wf.current_step_id = steps[1].id
        await session.commit()

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[1], "idle")

    context = mgr.calls[0][1] or ""
    # Inherits the *named* step, not the immediately preceding one.
    assert "ALPHA-FROM-STEP-ONE" in context
    assert "BETA-FROM-STEP-TWO" not in context


async def test_context_mode_none_cuts_off_upstream_material() -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(
        kinds=["task", "task"], step_kwargs=[{}, {"context_mode": "none"}]
    )
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agents[0])
        assert agent is not None
        agent.result_summary = "UPSTREAM-PAYLOAD"
        await session.commit()

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "idle")

    assert "UPSTREAM-PAYLOAD" not in (mgr.calls[0][1] or "")


async def test_step_capability_overrides_and_workflow_role_apply_at_launch() -> None:
    """Step toggles and the workflow role land on the agent before it runs."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession, Role
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    async with SessionLocal() as session:
        role = Role(name="Terse", system_prompt="Answer in one line.")
        session.add(role)
        await session.commit()
        await session.refresh(role)
        role_id = role.id

    wf_id, agents = await _seed_linear_workflow(
        kinds=["task", "task"],
        step_kwargs=[{}, {"use_mcp": False, "use_memory": False}],
        role_id=role_id,
    )

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "idle")

    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agents[1])
        assert agent is not None
        assert agent.use_mcp is False
        assert agent.use_memory is False
        assert agent.use_skills is True  # not overridden → inherited
        assert agent.role_id == role_id  # workflow-wide persona


async def test_workflow_events_carry_status_for_notifications() -> None:
    """workflow.changed carries status/name so the client can notify on it."""
    from precursor.backend.services.events import get_bus, publish_workflow_changed

    await _ensure_schema()
    async with get_bus().subscribe() as queue:
        await publish_workflow_changed(7, status="awaiting_approval", name="Joke pipeline")
        event = queue.get_nowait()
    # The bus whitelists payload keys, so these must survive the round trip or
    # the client can never notify on a parked run.
    assert event["type"] == "workflow.changed"
    assert event["workflow_id"] == 7
    assert event["status"] == "awaiting_approval"
    assert event["name"] == "Joke pipeline"


# --- Inline steps ------------------------------------------------------------
# An inline step is a one-off task whose prompt lives with the step. The runtime
# is agent-keyed, so it still needs an execution vessel — but that vessel is
# marked ``inline``, hidden from the Agents roster, reused across saves, and
# deleted with the step.


async def _make_workflow(client, steps: list[dict], name: str = "Inline pipeline") -> int:
    resp = client.post("/api/workflows", json={"name": name, "steps": steps})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


async def test_inline_step_creates_a_hidden_vessel() -> None:
    """An inline step gets an agent to run it, but not one you have to manage."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.models.workflow import WorkflowStep

    with TestClient(create_app()) as client:
        await _set_agents_enabled(True)
        wf_id = await _make_workflow(
            client, [{"kind": "inline", "task": "Translate the text into French"}]
        )

        async with SessionLocal() as session:
            step = (
                await session.execute(select(WorkflowStep).where(WorkflowStep.workflow_id == wf_id))
            ).scalar_one()
            assert step.kind == "inline"
            assert step.agent_id is not None
            agent = await session.get(AgentSession, step.agent_id)
            assert agent is not None
            assert agent.inline is True
            assert agent.task_prompt == "Translate the text into French"

        # …and it stays out of the Agents roster.
        roster = client.get("/api/agents").json()
        assert all(a["id"] != step.agent_id for a in roster)


async def test_inline_step_reuses_its_vessel_across_saves() -> None:
    """Re-saving edits the same vessel instead of minting a new one each time."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.models.workflow import WorkflowStep

    with TestClient(create_app()) as client:
        await _set_agents_enabled(True)
        wf_id = await _make_workflow(client, [{"kind": "inline", "task": "First draft"}])

        async with SessionLocal() as session:
            first = (
                await session.execute(select(WorkflowStep).where(WorkflowStep.workflow_id == wf_id))
            ).scalar_one()
            vessel_id = first.agent_id

        resp = client.patch(
            f"/api/workflows/{wf_id}",
            json={"steps": [{"kind": "inline", "agent_id": vessel_id, "task": "Second draft"}]},
        )
        assert resp.status_code == 200

        async with SessionLocal() as session:
            step = (
                await session.execute(select(WorkflowStep).where(WorkflowStep.workflow_id == wf_id))
            ).scalar_one()
            assert step.agent_id == vessel_id  # same vessel, history intact
            agent = await session.get(AgentSession, vessel_id)
            assert agent is not None
            assert agent.task_prompt == "Second draft"
            # No second vessel was left behind for this workflow.
            owned = (
                (
                    await session.execute(
                        select(WorkflowStep.agent_id).where(WorkflowStep.workflow_id == wf_id)
                    )
                )
                .scalars()
                .all()
            )
            assert list(owned) == [vessel_id]


async def test_removing_an_inline_step_deletes_its_vessel() -> None:
    """A vessel with no owning step must not linger as an invisible agent."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.models.workflow import WorkflowStep

    with TestClient(create_app()) as client:
        await _set_agents_enabled(True)
        wf_id = await _make_workflow(client, [{"kind": "inline", "task": "Throwaway"}])
        async with SessionLocal() as session:
            vessel_id = (
                await session.execute(
                    select(WorkflowStep.agent_id).where(WorkflowStep.workflow_id == wf_id)
                )
            ).scalar_one()

        # Replace the inline step with a plain agent-backed one.
        async with SessionLocal() as session:
            keeper = AgentSession(title="Reusable", task_prompt="do", status="idle")
            session.add(keeper)
            await session.commit()
            await session.refresh(keeper)
            keeper_id = keeper.id

        resp = client.patch(f"/api/workflows/{wf_id}", json={"steps": [{"agent_id": keeper_id}]})
        assert resp.status_code == 200

        async with SessionLocal() as session:
            assert await session.get(AgentSession, vessel_id) is None


async def test_deleting_a_workflow_deletes_its_inline_vessels() -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.models.workflow import WorkflowStep

    with TestClient(create_app()) as client:
        await _set_agents_enabled(True)
        wf_id = await _make_workflow(client, [{"kind": "inline", "task": "Ephemeral"}])
        async with SessionLocal() as session:
            vessel_id = (
                await session.execute(
                    select(WorkflowStep.agent_id).where(WorkflowStep.workflow_id == wf_id)
                )
            ).scalar_one()
        assert client.delete(f"/api/workflows/{wf_id}").status_code in (200, 204)

        async with SessionLocal() as session:
            assert await session.get(AgentSession, vessel_id) is None


async def test_inline_step_is_a_content_producer() -> None:
    """An inline step hands its output downstream exactly like a task step."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["inline", "task"])
    async with SessionLocal() as session:
        producer = await session.get(AgentSession, agents[0])
        assert producer is not None
        producer.result_summary = "INLINE-OUTPUT"
        await session.commit()

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "idle")

    assert [c[0] for c in mgr.calls] == [agents[1]]
    assert "INLINE-OUTPUT" in (mgr.calls[0][1] or "")


async def test_workflow_defaults_from_settings_seed_new_steps() -> None:
    """Settings → Workflows decides where a fresh workflow/step starts.

    A default of *off* must be written onto the step explicitly: leaving it null
    would mean "inherit the agent", which quietly turns the capability back on.
    A default of *on* stays null so the step simply inherits.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow, WorkflowStep

    with TestClient(create_app()) as client:
        await _set_agents_enabled(True)
        assert (
            client.put(
                "/api/settings",
                json={
                    "workflows_default_use_mcp": False,
                    "workflows_default_use_skills": True,
                    "workflows_default_use_memory": False,
                    "workflows_default_step_timeout_seconds": 900,
                },
            ).status_code
            == 200
        )

        body = client.get("/api/settings").json()
        assert body["workflows_default_use_mcp"] is False
        assert body["workflows_default_step_timeout_seconds"] == 900

        wf_id = await _make_workflow(
            client, [{"kind": "inline", "task": "Do the thing"}], name="Seeded"
        )
        async with SessionLocal() as session:
            wf = await session.get(Workflow, wf_id)
            assert wf is not None
            assert wf.step_timeout_seconds == 900  # watchdog seeded
            step = (
                await session.execute(select(WorkflowStep).where(WorkflowStep.workflow_id == wf_id))
            ).scalar_one()
            assert step.use_mcp is False  # default off → forced off
            assert step.use_memory is False
            assert step.use_skills is None  # default on → inherit the agent

        # An explicit value in the payload still wins over the default.
        assert (
            client.patch(
                f"/api/workflows/{wf_id}",
                json={"steps": [{"kind": "inline", "task": "Do it", "use_mcp": True}]},
            ).status_code
            == 200
        )
        async with SessionLocal() as session:
            step = (
                await session.execute(select(WorkflowStep).where(WorkflowStep.workflow_id == wf_id))
            ).scalar_one()
            assert step.use_mcp is True


async def test_gate_can_be_an_inline_prompt() -> None:
    """A gate authored in the step is private to it, exactly like an inline task.

    The rule is "was the prompt written here?", not "what kind is the step" — so
    a one-off check doesn't leave a reusable agent behind either.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.models.workflow import WorkflowStep

    with TestClient(create_app()) as client:
        await _set_agents_enabled(True)
        wf_id = await _make_workflow(
            client,
            [
                {"kind": "inline", "task": "Tell a joke"},
                {"kind": "gate", "task": "Is the joke safe for kids?"},
            ],
            name="Inline gate",
        )

        async with SessionLocal() as session:
            steps = (
                (
                    await session.execute(
                        select(WorkflowStep)
                        .where(WorkflowStep.workflow_id == wf_id)
                        .order_by(WorkflowStep.position)
                    )
                )
                .scalars()
                .all()
            )
            gate = steps[1]
            assert gate.kind == "gate"
            assert gate.agent_id is not None
            vessel = await session.get(AgentSession, gate.agent_id)
            assert vessel is not None
            assert vessel.inline is True  # private to the step
            assert vessel.task_prompt == "Is the joke safe for kids?"
            gate_vessel_id = vessel.id

        # Hidden from the roster, like any other vessel.
        roster = client.get("/api/agents").json()
        assert all(a["id"] != gate_vessel_id for a in roster)

        # Re-saving reuses it rather than minting a second one.
        assert (
            client.patch(
                f"/api/workflows/{wf_id}",
                json={
                    "steps": [
                        {"kind": "inline", "task": "Tell a joke"},
                        {
                            "kind": "gate",
                            "agent_id": gate_vessel_id,
                            "task": "Is it safe AND funny?",
                        },
                    ]
                },
            ).status_code
            == 200
        )
        async with SessionLocal() as session:
            steps = (
                (
                    await session.execute(
                        select(WorkflowStep)
                        .where(WorkflowStep.workflow_id == wf_id)
                        .order_by(WorkflowStep.position)
                    )
                )
                .scalars()
                .all()
            )
            assert steps[1].agent_id == gate_vessel_id
            vessel = await session.get(AgentSession, gate_vessel_id)
            assert vessel is not None
            assert vessel.task_prompt == "Is it safe AND funny?"

        # Dropping the gate removes its vessel.
        assert (
            client.patch(
                f"/api/workflows/{wf_id}",
                json={"steps": [{"kind": "inline", "task": "Tell a joke"}]},
            ).status_code
            == 200
        )
        async with SessionLocal() as session:
            assert await session.get(AgentSession, gate_vessel_id) is None


async def test_referencing_an_existing_agent_never_makes_a_vessel() -> None:
    """Picking a reusable agent must leave it reusable and visible."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    with TestClient(create_app()) as client:
        await _set_agents_enabled(True)
        async with SessionLocal() as session:
            keeper = AgentSession(title="Reusable checker", task_prompt="check", status="idle")
            session.add(keeper)
            await session.commit()
            await session.refresh(keeper)
            keeper_id = keeper.id

        wf_id = await _make_workflow(client, [{"kind": "gate", "agent_id": keeper_id}])
        async with SessionLocal() as session:
            agent = await session.get(AgentSession, keeper_id)
            assert agent is not None
            assert agent.inline is False

        # Deleting the workflow must not take the shared agent with it.
        assert client.delete(f"/api/workflows/{wf_id}").status_code in (200, 204)
        async with SessionLocal() as session:
            assert await session.get(AgentSession, keeper_id) is not None


async def test_inline_vessel_is_named_after_its_step() -> None:
    """A hidden vessel has no separately-typed name: it follows the step label,
    falling back to the opening of its own prompt."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.models.workflow import WorkflowStep

    with TestClient(create_app()) as client:
        await _set_agents_enabled(True)
        wf_id = await _make_workflow(
            client,
            [
                {"kind": "inline", "task": "Translate it", "name": "Translate"},
                {"kind": "inline", "task": "Proofread the translation"},
            ],
            name="Naming",
        )
        async with SessionLocal() as session:
            steps = (
                (
                    await session.execute(
                        select(WorkflowStep)
                        .where(WorkflowStep.workflow_id == wf_id)
                        .order_by(WorkflowStep.position)
                    )
                )
                .scalars()
                .all()
            )
            labelled = await session.get(AgentSession, steps[0].agent_id)
            assert labelled is not None
            assert labelled.title == "Translate"  # took the step label

            unlabelled = await session.get(AgentSession, steps[1].agent_id)
            assert unlabelled is not None
            assert unlabelled.title == "Proofread the translation"  # fell back


async def test_step_can_author_a_reusable_agent() -> None:
    """``reusable`` mints a real agent from the builder instead of a vessel.

    Same "the prompt was written here" payload as an inline step — only where the
    resulting agent *lives* differs, so a pipeline can create the agent it needs
    without a detour through the Agents section.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.models.workflow import WorkflowStep

    with TestClient(create_app()) as client:
        await _set_agents_enabled(True)
        wf_id = await _make_workflow(
            client,
            [
                {
                    "kind": "task",
                    "task": "Summarise the input in three bullets",
                    "title": "Summariser",
                    "name": "Summarise",
                    "reusable": True,
                }
            ],
            name="Creates an agent",
        )

        async with SessionLocal() as session:
            step = (
                await session.execute(select(WorkflowStep).where(WorkflowStep.workflow_id == wf_id))
            ).scalar_one()
            agent = await session.get(AgentSession, step.agent_id)
            assert agent is not None
            assert agent.inline is False  # a real agent, not a private vessel
            # Its own name wins over the step label — that name is how it is
            # picked everywhere else.
            assert agent.title == "Summariser"
            assert agent.task_prompt == "Summarise the input in three bullets"
            assert agent.status == "waiting"  # parked until the workflow drives it
            agent_id = agent.id

        # It joins the roster, so another step or workflow can pick it.
        assert any(a["id"] == agent_id for a in client.get("/api/agents").json())


async def test_authored_reusable_agent_survives_its_step() -> None:
    """The orphan sweep only claims vessels — a created agent is not disposable."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.models.workflow import WorkflowStep

    with TestClient(create_app()) as client:
        await _set_agents_enabled(True)
        wf_id = await _make_workflow(
            client,
            [{"kind": "task", "task": "Draft it", "title": "Drafter", "reusable": True}],
            name="Outlives its step",
        )
        async with SessionLocal() as session:
            agent_id = (
                await session.execute(
                    select(WorkflowStep.agent_id).where(WorkflowStep.workflow_id == wf_id)
                )
            ).scalar_one()

        # Re-saving as a plain reference must not re-mint or disturb it: the step
        # reopens as "existing agent" once the agent is real.
        assert (
            client.patch(
                f"/api/workflows/{wf_id}", json={"steps": [{"agent_id": agent_id}]}
            ).status_code
            == 200
        )
        async with SessionLocal() as session:
            drafters = (
                (
                    await session.execute(
                        select(AgentSession.id).where(AgentSession.title == "Drafter")
                    )
                )
                .scalars()
                .all()
            )
            assert list(drafters) == [agent_id]

        # And deleting the workflow leaves it standing.
        assert client.delete(f"/api/workflows/{wf_id}").status_code in (200, 204)
        async with SessionLocal() as session:
            survivor = await session.get(AgentSession, agent_id)
            assert survivor is not None
            assert survivor.inline is False


async def test_authoring_defaults_to_a_private_vessel() -> None:
    """Omitting ``reusable`` keeps the established inline behaviour."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.models.workflow import WorkflowStep

    with TestClient(create_app()) as client:
        await _set_agents_enabled(True)
        wf_id = await _make_workflow(
            client, [{"kind": "gate", "task": "Is it accurate?"}], name="Default vessel"
        )
        async with SessionLocal() as session:
            step = (
                await session.execute(select(WorkflowStep).where(WorkflowStep.workflow_id == wf_id))
            ).scalar_one()
            agent = await session.get(AgentSession, step.agent_id)
            assert agent is not None
            assert agent.inline is True


async def test_workflow_icon_can_be_set_and_cleared() -> None:
    """An icon is optional: sending null clears it, omitting it leaves it alone.

    Regression: the update handler used ``if payload.icon is not None``, so a
    null could never remove an icon once one had been chosen.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow

    with TestClient(create_app()) as client:
        await _set_agents_enabled(True)
        wf_id = await _make_workflow(client, [{"kind": "inline", "task": "x"}], name="Iconic")

        assert client.patch(f"/api/workflows/{wf_id}", json={"icon": "🚀"}).status_code == 200
        async with SessionLocal() as session:
            wf = await session.get(Workflow, wf_id)
            assert wf is not None and wf.icon == "🚀"

        # Omitting the field leaves it untouched…
        assert client.patch(f"/api/workflows/{wf_id}", json={"name": "Iconic!"}).status_code == 200
        async with SessionLocal() as session:
            wf = await session.get(Workflow, wf_id)
            assert wf is not None and wf.icon == "🚀"

        # …and an explicit null clears it.
        assert client.patch(f"/api/workflows/{wf_id}", json={"icon": None}).status_code == 200
        async with SessionLocal() as session:
            wf = await session.get(Workflow, wf_id)
            assert wf is not None and wf.icon is None


async def test_run_trace_flags_private_vessels() -> None:
    """A trace says whether its agent is reusable, so the UI knows what to link.

    An inline step's vessel isn't in the Agents list, so offering "Open" for it
    sends the operator to a page for an agent they don't manage.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.models.workflow import WorkflowStep

    with TestClient(create_app()) as client:
        await _set_agents_enabled(True)
        async with SessionLocal() as session:
            shared = AgentSession(title="Reusable", task_prompt="do", status="idle")
            session.add(shared)
            await session.commit()
            await session.refresh(shared)
            shared_id = shared.id

        wf_id = await _make_workflow(
            client,
            [{"agent_id": shared_id}, {"kind": "inline", "task": "one-off"}],
            name="Mixed",
        )
        # Seed a run with a trace for both steps. (``_open_run_for`` only traces
        # the workflow's *current* step, and an API-created workflow is a draft.)
        async with SessionLocal() as session:
            from datetime import UTC, datetime

            from precursor.backend.models.workflow import Workflow, WorkflowRun, WorkflowRunStep

            run = WorkflowRun(
                workflow_id=wf_id, run_number=1, status="running", started_at=datetime.now(UTC)
            )
            session.add(run)
            await session.flush()
            wf = await session.get(Workflow, wf_id)
            assert wf is not None
            wf.current_run_id = run.id
            steps = (
                (
                    await session.execute(
                        select(WorkflowStep)
                        .where(WorkflowStep.workflow_id == wf_id)
                        .order_by(WorkflowStep.position)
                    )
                )
                .scalars()
                .all()
            )
            for step in steps:
                session.add(
                    WorkflowRunStep(
                        run_id=run.id,
                        position=step.position,
                        kind=step.kind,
                        label=f"step {step.position}",
                        agent_id=step.agent_id,
                        attempt=1,
                        status="completed",
                        started_at=datetime.now(UTC),
                    )
                )
            await session.commit()

        runs = client.get(f"/api/workflows/{wf_id}/runs").json()
        traces = {t["position"]: t for t in runs[0]["step_runs"]}
        assert traces[0]["agent_inline"] is False  # reusable → linkable
        assert traces[1]["agent_inline"] is True  # private vessel → not linkable


async def test_workflow_archive_and_agent_workflow_links() -> None:
    """Workflows archive like everything else, and an agent knows its pipelines."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    with TestClient(create_app()) as client:
        await _set_agents_enabled(True)
        async with SessionLocal() as session:
            shared = AgentSession(title="Shared checker", task_prompt="check", status="idle")
            session.add(shared)
            await session.commit()
            await session.refresh(shared)
            shared_id = shared.id

        a_id = await _make_workflow(client, [{"agent_id": shared_id}], name="Alpha")
        b_id = await _make_workflow(client, [{"agent_id": shared_id}], name="Beta")
        await _make_workflow(client, [{"kind": "inline", "task": "solo"}], name="Gamma")

        # The agent reports both pipelines that reference it, by name.
        links = client.get(f"/api/agents/{shared_id}/workflows").json()
        assert [w["name"] for w in links] == ["Alpha", "Beta"]

        # Archiving hides a workflow from the list and surfaces it in the archive.
        assert client.post(f"/api/workflows/{a_id}/archive").status_code == 200
        active = [w["name"] for w in client.get("/api/workflows").json()]
        assert "Alpha" not in active and "Beta" in active
        archived = [w["name"] for w in client.get("/api/workflows/archived").json()]
        assert archived == ["Alpha"]

        # An archived workflow no longer counts as a live reference.
        links = client.get(f"/api/agents/{shared_id}/workflows").json()
        assert [w["name"] for w in links] == ["Beta"]

        # …and unarchiving restores both.
        assert client.post(f"/api/workflows/{a_id}/unarchive").status_code == 200
        assert client.get("/api/workflows/archived").json() == []
        links = client.get(f"/api/agents/{shared_id}/workflows").json()
        assert [w["name"] for w in links] == ["Alpha", "Beta"]

        # A private inline vessel is never offered as a reusable agent link.
        assert b_id != a_id


async def test_agent_list_carries_workflow_count() -> None:
    """The agents list reports each agent's live workflow usage in one query.

    Fetching it per card would be one request per agent, so the count rides on
    the list payload; only the *names* are fetched on demand.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    with TestClient(create_app()) as client:
        await _set_agents_enabled(True)
        async with SessionLocal() as session:
            used = AgentSession(title="Used twice", task_prompt="do", status="idle")
            spare = AgentSession(title="Unused", task_prompt="do", status="idle")
            session.add_all([used, spare])
            await session.commit()
            await session.refresh(used)
            await session.refresh(spare)
            used_id, spare_id = used.id, spare.id

        a_id = await _make_workflow(client, [{"agent_id": used_id}], name="One")
        await _make_workflow(client, [{"agent_id": used_id}], name="Two")

        rows = {a["id"]: a for a in client.get("/api/agents").json()}
        assert rows[used_id]["workflow_count"] == 2
        assert rows[spare_id]["workflow_count"] == 0
        # The single-agent read agrees with the list.
        assert client.get(f"/api/agents/{used_id}").json()["workflow_count"] == 2

        # Archiving a workflow drops it from the count.
        assert client.post(f"/api/workflows/{a_id}/archive").status_code == 200
        rows = {a["id"]: a for a in client.get("/api/agents").json()}
        assert rows[used_id]["workflow_count"] == 1

        # A private inline vessel isn't in the roster at all, so it can't skew it.
        wf_id = await _make_workflow(client, [{"kind": "inline", "task": "solo"}], name="Three")
        steps = client.get(f"/api/workflows/{wf_id}").json()["steps"]
        inline_id = steps[0]["agent_id"]
        assert inline_id is not None
        listed = {a["id"] for a in client.get("/api/agents").json()}
        assert {used_id, spare_id} <= listed
        assert inline_id not in listed


# --- Getting a stopped run moving again --------------------------------------
#
# Two dead ends the pipeline used to have no answer for. A step's agent *blocks*
# on a question it can't resolve, and resuming re-drove it blind — straight back
# into the same question. Or a step *fails* under the `fail` policy, and the only
# way forward was re-running the whole pipeline from step 1, discarding every
# good step before the bad one (and paying for them again).


async def test_resume_injects_the_humans_answer_into_the_blocked_step() -> None:
    """Resuming a blocked step carries the answer, quoting what it asked."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.models.workflow import Workflow
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task", "task"])
    await _open_run_for(wf_id)

    # The step's agent parks itself on a question, which pauses the run.
    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agents[0])
        assert agent is not None
        agent.blocked_question = "Which sheet should I use?"
        await session.commit()
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "blocked")
    assert mgr.calls == []
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None and wf.status == "paused"

    mgr2 = _FakeWorkflowManager()
    async with SessionLocal() as session:
        await wf_mod.resume_workflow(session, mgr2, wf_id, guidance="  Use the EMEA sheet.  ")

    # Same step re-driven, now carrying both the question and its answer.
    assert [c[0] for c in mgr2.calls] == [agents[0]]
    context = mgr2.calls[0][1] or ""
    assert "Which sheet should I use?" in context
    assert "Use the EMEA sheet." in context
    assert "Do not ask it again" in context


async def test_resume_without_an_answer_still_just_resumes() -> None:
    """A plain pause needs no guidance — the bodyless resume is unchanged."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task", "task"])
    await _open_run_for(wf_id)
    async with SessionLocal() as session:
        await wf_mod.pause_workflow(session, wf_id)

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        await wf_mod.resume_workflow(session, mgr, wf_id)

    assert [c[0] for c in mgr.calls] == [agents[0]]
    assert "a human answered it" not in (mgr.calls[0][1] or "")


async def test_retry_step_reopens_the_run_at_the_failed_step() -> None:
    """A failed run picks back up at the step that broke, as a new attempt."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task", "task"])
    run_id = await _open_run_for(wf_id)

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "failed")
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None and wf.status == "failed" and wf.error

    # Retry with no position: the step whose failure stopped the run.
    mgr2 = _FakeWorkflowManager()
    async with SessionLocal() as session:
        await wf_mod.retry_step(session, mgr2, wf_id, guidance="Use the v2 endpoint.")

    assert [c[0] for c in mgr2.calls] == [agents[0]]
    assert "Use the v2 endpoint." in (mgr2.calls[0][1] or "")

    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None
        assert wf.status == "running"
        # The stale outcome is cleared — the run is going again, not finished.
        assert wf.error is None
        assert wf.finished_at is None

    # Same run, with the retry appended as a second attempt at that position
    # rather than starting a fresh run that discards the trace.
    steps = await _run_steps(run_id)
    step0 = [s for s in steps if s.position == 0]
    assert [s.attempt for s in step0] == [1, 2]
    assert step0[0].status == "failed"
    assert step0[1].status == "running"


async def test_retry_step_targets_an_explicit_position() -> None:
    """Retrying names its step, so an earlier one can be redone deliberately."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task", "task", "task"])
    await _open_run_for(wf_id)

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "failed")

    mgr2 = _FakeWorkflowManager()
    async with SessionLocal() as session:
        await wf_mod.retry_step(session, mgr2, wf_id, position=1)
    assert [c[0] for c in mgr2.calls] == [agents[1]]


async def test_retry_step_resets_the_spent_retry_budget() -> None:
    """A manual retry starts fresh instead of inheriting an exhausted counter."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import WorkflowStep
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(
        kinds=["task", "task"],
        step_kwargs=[{"on_error": "retry", "max_retries": 1}, {}],
    )
    await _open_run_for(wf_id)

    # Burn the automatic budget: first failure retries, second stops the run.
    for _ in range(2):
        mgr = _FakeWorkflowManager()
        async with SessionLocal() as session:
            wf = await wf_mod._load_workflow(session, wf_id)
            assert wf is not None
            await wf_mod._advance_one(session, mgr, wf, agents[0], "failed")

    async with SessionLocal() as session:
        step = (
            (
                await session.execute(
                    select(WorkflowStep).where(
                        WorkflowStep.workflow_id == wf_id, WorkflowStep.position == 0
                    )
                )
            )
            .scalars()
            .one()
        )
        assert step.retry_count == 1

    mgr2 = _FakeWorkflowManager()
    async with SessionLocal() as session:
        await wf_mod.retry_step(session, mgr2, wf_id)

    async with SessionLocal() as session:
        step = (
            (
                await session.execute(
                    select(WorkflowStep).where(
                        WorkflowStep.workflow_id == wf_id, WorkflowStep.position == 0
                    )
                )
            )
            .scalars()
            .one()
        )
        assert step.retry_count == 0


async def test_retry_step_refuses_while_the_run_is_live() -> None:
    """Retrying under a running coordinator would race it, so it's a no-op."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, _agents = await _seed_linear_workflow(kinds=["task", "task"])
    await _open_run_for(wf_id)

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        await wf_mod.retry_step(session, mgr, wf_id)
    assert mgr.calls == []
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None and wf.status == "running"


async def test_retry_and_resume_endpoints_accept_an_optional_body() -> None:
    """Both take a body or none, so scripted callers keep working."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    try:
        with TestClient(create_app()) as client:
            await _set_agents_enabled(True)
            wf_id, agents = await _seed_linear_workflow(kinds=["task", "task"])
            await _open_run_for(wf_id)

            mgr = _FakeWorkflowManager()
            async with SessionLocal() as session:
                wf = await wf_mod._load_workflow(session, wf_id)
                assert wf is not None
                await wf_mod._advance_one(session, mgr, wf, agents[0], "failed")

            assert (
                client.post(f"/api/workflows/{wf_id}/retry", json={"input": "try v2"}).status_code
                == 200
            )
            async with SessionLocal() as session:
                wf = await session.get(Workflow, wf_id)
                assert wf is not None and wf.status == "running"

            # Bodyless resume on a paused run.
            async with SessionLocal() as session:
                await wf_mod.pause_workflow(session, wf_id)
            assert client.post(f"/api/workflows/{wf_id}/resume").status_code == 200
    finally:
        # Leave the shared scratch DB with agents off: a later app startup
        # would otherwise try to launch the real Copilot runtime and hang.
        await _set_agents_enabled(False)


async def test_step_attempt_events_are_sliced_to_their_own_attempt() -> None:
    """Each attempt shows its own activity, not the agent's whole history.

    Events are archived per *agent*, so a step re-driven four times has one
    continuous stream. Without slicing, every trace row would replay all of it
    and the one attempt you're debugging would be indistinguishable.
    """
    import json
    from datetime import UTC, datetime, timedelta

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentEventRecord
    from precursor.backend.models.workflow import WorkflowRunStep
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task"])
    run_id = await _open_run_for(wf_id)
    agent_id = agents[0]
    assert agent_id is not None

    base = datetime.now(UTC).replace(microsecond=0)

    async with SessionLocal() as session:
        # Attempt 1 ran and finished; attempt 2 is still open.
        first = (
            (await session.execute(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id)))
            .scalars()
            .one()
        )
        first.started_at = base
        first.finished_at = base + timedelta(seconds=30)
        second = WorkflowRunStep(
            run_id=run_id,
            position=0,
            kind="task",
            label="Step 0",
            agent_id=agent_id,
            attempt=2,
            status="running",
            started_at=base + timedelta(seconds=60),
        )
        session.add(second)
        for offset, kind in (
            (-600, "before_the_run"),  # an earlier, unrelated turn
            (5, "reasoning"),
            (10, "ToolExecutionStartData"),
            (70, "assistant_message"),  # belongs to attempt 2
        ):
            session.add(
                AgentEventRecord(
                    agent_session_id=agent_id,
                    payload=json.dumps({"kind": kind}),
                    created_at=base + timedelta(seconds=offset),
                )
            )
        await session.commit()
        first_id, second_id = first.id, second.id

    async with SessionLocal() as session:
        one = await wf_mod.step_attempt_events(session, wf_id, first_id)
        two = await wf_mod.step_attempt_events(session, wf_id, second_id)

    assert one is not None and two is not None
    # Bounded at both ends: the earlier turn and attempt 2's work are excluded.
    assert [e["kind"] for e in one] == ["reasoning", "ToolExecutionStartData"]
    # An open attempt has no upper bound, so it collects everything since it began.
    assert [e["kind"] for e in two] == ["assistant_message"]
    # Every event carries a timestamp so the UI can order and label it.
    assert all(e.get("at") for e in one)


async def test_step_attempt_events_reject_a_foreign_workflow() -> None:
    """An attempt id from another workflow is a 404, not someone else's trace."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import WorkflowRunStep
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    mine, _ = await _seed_linear_workflow(kinds=["task"])
    theirs, _ = await _seed_linear_workflow(kinds=["task"])
    their_run = await _open_run_for(theirs)

    async with SessionLocal() as session:
        their_step = (
            (
                await session.execute(
                    select(WorkflowRunStep).where(WorkflowRunStep.run_id == their_run)
                )
            )
            .scalars()
            .one()
        )
        step_id = their_step.id

    async with SessionLocal() as session:
        assert await wf_mod.step_attempt_events(session, mine, step_id) is None
        assert await wf_mod.step_attempt_events(session, theirs, step_id) is not None


async def test_workflow_approval_policy_applies_to_each_step_agent() -> None:
    """The pipeline's stance wins for the run, without rewriting shared agents.

    A step that stops at a permission gate parks the whole run until a human
    answers — which a scheduled or webhook-fired workflow has nobody to do. The
    policy is therefore set once on the workflow and pushed onto whichever agent
    is about to run, rather than stamped onto agents that are used elsewhere too.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.models.workflow import Workflow, WorkflowStep
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task", "task"])
    agent_id = agents[0]
    assert agent_id is not None

    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        agent.approval_policy = "manual"
        wf = await session.get(Workflow, wf_id)
        assert wf is not None
        wf.approval_policy = "autonomous"
        await session.commit()

    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        step = (
            (
                await session.execute(
                    select(WorkflowStep).where(
                        WorkflowStep.workflow_id == wf_id, WorkflowStep.position == 0
                    )
                )
            )
            .scalars()
            .one()
        )
        await wf_mod._apply_step_overrides(session, wf, step)
        await session.commit()

    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        assert agent.approval_policy == "autonomous"

    # A workflow that states no policy leaves the agent's own alone.
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None
        wf.approval_policy = None
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        agent.approval_policy = "manual"
        await session.commit()

    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        step = (
            (
                await session.execute(
                    select(WorkflowStep).where(
                        WorkflowStep.workflow_id == wf_id, WorkflowStep.position == 0
                    )
                )
            )
            .scalars()
            .one()
        )
        await wf_mod._apply_step_overrides(session, wf, step)
        await session.commit()

    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        assert agent.approval_policy == "manual"


async def test_workflow_read_surfaces_a_steps_parked_permission() -> None:
    """The board must be able to answer a gate on an *inline* step.

    An inline step's agent is hidden from the Agents roster, so if the workflow
    payload doesn't carry the pending request there is nowhere in the app to
    approve it and the step stalls until the watchdog kills it.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import WorkflowStep
    from precursor.backend.services.agents.manager import get_agent_manager

    await _ensure_schema()
    try:
        with TestClient(create_app()) as client:
            await _set_agents_enabled(True)
            wf_id, agents = await _seed_linear_workflow(kinds=["task"])
            agent_id = agents[0]
            assert agent_id is not None
            async with SessionLocal() as session:
                step = (
                    (
                        await session.execute(
                            select(WorkflowStep).where(WorkflowStep.workflow_id == wf_id)
                        )
                    )
                    .scalars()
                    .one()
                )
                step.kind = "inline"
                await session.commit()

            # Stand in for a live session parked on a tool-permission request.
            mgr = get_agent_manager()
            original = mgr.live_activity
            mgr.live_activity = lambda ids: {  # type: ignore[method-assign]
                aid: {
                    "active_tool": None,
                    "active_tool_count": 0,
                    "active_narration": "sending the email",
                    "pending_permission": {
                        "request_id": "req-1",
                        "title": "Run workiq-do_action",
                        "data": {"tool": "workiq-do_action", "server": "workiq"},
                    },
                }
                for aid in ids
            }
            try:
                body = client.get(f"/api/workflows/{wf_id}").json()
            finally:
                mgr.live_activity = original  # type: ignore[method-assign]

        agent = body["steps"][0]["agent"]
        assert agent["pending_permission"]["request_id"] == "req-1"
        # The whole payload travels — the board has no event stream to mine it from.
        assert agent["pending_permission"]["data"]["tool"] == "workiq-do_action"
        # Live narration rides along too (it was silently null before).
        assert agent["active_narration"] == "sending the email"
    finally:
        await _set_agents_enabled(False)


async def test_a_permission_gate_does_not_park_the_run() -> None:
    """Waiting on a tool decision is not the same as being blocked.

    The turn is still alive and resumes by itself once the gate is answered, so
    pausing the run for it meant *every tool call* closed the step's trace and
    demanded a manual "Resume" — an agent making five calls blocked five times.
    The run stays running with its trace open; only the card is surfaced.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task", "task"])
    run_id = await _open_run_for(wf_id)
    agent_id = agents[0]
    assert agent_id is not None

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agent_id, "needs_approval")

    # Nothing re-driven, nothing recorded, nothing parked.
    assert mgr.calls == []
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None
        assert wf.status == "running"
        assert wf.current_step_id is not None
    steps = await _run_steps(run_id)
    assert [s.status for s in steps] == ["running"]
    assert steps[0].finished_at is None

    # Several gates in one step leave exactly one attempt, not one per call.
    for _ in range(3):
        async with SessionLocal() as session:
            wf = await wf_mod._load_workflow(session, wf_id)
            assert wf is not None
            await wf_mod._advance_one(session, mgr, wf, agent_id, "needs_approval")
    assert len(await _run_steps(run_id)) == 1

    # And the turn that follows advances the pipeline exactly as normal.
    mgr2 = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr2, wf, agent_id, "idle")
    assert [c[0] for c in mgr2.calls] == [agents[1]]


async def test_a_raised_question_still_parks_the_run() -> None:
    """The other half of the split: ``blocked`` is a real stop.

    The agent ended its turn asking something, so nothing resumes on its own —
    the run parks and waits for a human to answer and re-drive it.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task", "task"])
    run_id = await _open_run_for(wf_id)

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agents[0], "blocked")

    assert mgr.calls == []
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None and wf.status == "paused"
    assert [s.status for s in await _run_steps(run_id)] == ["blocked"]


async def test_approving_a_permission_on_a_paused_run_puts_it_back_in_flight() -> None:
    """A run parked *before* the split (or by a raised question) still recovers.

    ``advance_for_agent`` only looks at running workflows, so resolving the gate
    of a paused run without restoring it would leave the approved agent
    finishing its turn into a pipeline that had stopped listening.
    """
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, agents = await _seed_linear_workflow(kinds=["task", "task"])
    run_id = await _open_run_for(wf_id)
    agent_id = agents[0]
    assert agent_id is not None

    mgr = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr, wf, agent_id, "blocked")
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None and wf.status == "paused"

    class _PermissionManager(_FakeWorkflowManager):
        def __init__(self) -> None:
            super().__init__()
            self.resolved: list[tuple[int, str, str]] = []

        async def resolve_permission(self, aid: int, request_id: str, decision: str) -> bool:
            self.resolved.append((aid, request_id, decision))
            return True

    pm = _PermissionManager()
    async with SessionLocal() as session:
        _wf, ok = await wf_mod.resolve_step_permission(
            session, pm, wf_id, request_id="req-1", decision="approve-once"
        )
    assert ok is True
    assert pm.resolved == [(agent_id, "req-1", "approve-once")]

    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        assert wf is not None and wf.status == "running"

    # The continuing turn gets an open trace to record into.
    steps = await _run_steps(run_id)
    assert [s.status for s in steps] == ["blocked", "running"]
    assert [s.attempt for s in steps] == [1, 2]

    mgr2 = _FakeWorkflowManager()
    async with SessionLocal() as session:
        wf = await wf_mod._load_workflow(session, wf_id)
        assert wf is not None
        await wf_mod._advance_one(session, mgr2, wf, agent_id, "idle")
    assert [c[0] for c in mgr2.calls] == [agents[1]]


async def test_resolving_a_stale_permission_reports_it_rather_than_pretending() -> None:
    """A card for a gate that's gone must say so, not silently do nothing."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.workflow import Workflow
    from precursor.backend.services.agents import workflow as wf_mod

    await _ensure_schema()
    wf_id, _agents = await _seed_linear_workflow(kinds=["task"])
    await _open_run_for(wf_id)

    class _NoMatchManager(_FakeWorkflowManager):
        async def resolve_permission(self, aid: int, request_id: str, decision: str) -> bool:
            return False

    async with SessionLocal() as session:
        wf, ok = await wf_mod.resolve_step_permission(
            session, _NoMatchManager(), wf_id, request_id="gone", decision="deny"
        )
    assert ok is False
    async with SessionLocal() as session:
        wf = await session.get(Workflow, wf_id)
        # Untouched: nothing was resolved, so nothing is resumed.
        assert wf is not None and wf.status == "running"


async def test_resolving_a_permission_unsticks_the_agent_status() -> None:
    """Answering a gate must return the agent to ``running``.

    ``needs_approval`` is *sticky*: the idle handler skips it so a trailing idle
    can't mask a genuinely parked agent. An agent left sitting in it therefore
    never reaches ``_on_idle`` — its turn finishes, the workflow is never told,
    and the step shows "Running" forever. The manager owns this reset so every
    caller gets it; when only the agents router did, approving from the workflow
    board silently wedged the run.
    """
    import asyncio

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.agents.manager import AgentManager

    await _ensure_schema()
    agent_id = await _make_agent(title="Gated", status="needs_approval")

    mgr = AgentManager()
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()

    class _Live:
        def __init__(self) -> None:
            self.pending = {"req-1": fut}
            self.pending_info: dict = {}
            self.session_approvals: set = set()
            self.grants: list = []

    mgr._live[agent_id] = _Live()  # type: ignore[assignment]
    # The decision object needs the SDK; the status reset is what's under test.
    mgr._decision = lambda decision: decision  # type: ignore[method-assign]

    assert await mgr.resolve_permission(agent_id, "req-1", "approve-once") is True
    assert fut.result() == "approve-once"

    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        assert agent.status == "running"

    # A request the runtime can't match changes nothing.
    assert await mgr.resolve_permission(agent_id, "gone", "deny") is False
