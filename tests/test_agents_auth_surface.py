"""Tests for surfacing the WorkIQ sign-in prompt on the Agents runtime path.

The Agents runtime used to *silently skip* an enabled-but-unauthenticated OAuth
server (WorkIQ), leaving the model to discover the tools were missing and
improvise an error. These cover the seams that now turn that into an in-app
``mcp_auth_required`` event (which drives the global sign-in banner), without
needing the live Copilot SDK:

- ``_catalog_mcp_configs`` reports the skipped server under ``auth_required``.
- ``_announce_auth_required`` emits once per server and re-fires after the
  server later authenticates (so a lapsed token prompts again).
- ``_auth_server_from_failed_tool`` only nags on a genuine WorkIQ auth failure.
- ``_auth_skipped_stamps`` / ``_blocked_on_missing_auth``: the credential
  fingerprint that lets a tool-less session be rebuilt once the user signs back
  in, and the guard that stops an explicitly-scoped step "succeeding" without
  the server it asked for.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.schemas.agent import AgentEvent
from precursor.backend.services.agents.manager import AgentManager, _LiveSession


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


async def test_catalog_reports_workiq_when_unauthenticated(monkeypatch) -> None:
    """An enabled OAuth server with no creds is surfaced via ``auth_required``."""
    from precursor.backend.services.agents import runtime
    from precursor.backend.services.mcp.client import get_mcp_client_manager

    # Initialise the schema (alembic upgrade runs on app startup).
    with TestClient(create_app()):
        pass

    # Pretend the SDK is loadable and represent any entry as a trivial config so
    # the test never touches the real Copilot SDK.
    monkeypatch.setattr(runtime, "load_sdk", lambda: object())
    monkeypatch.setattr(
        AgentManager,
        "_entry_to_sdk_config",
        staticmethod(lambda sdk, entry, token: {"type": "http", "headers": {}}),
    )

    async def _no_creds(name: str) -> None:
        return None

    monkeypatch.setattr(AgentManager, "_oauth_bearer_header", staticmethod(_no_creds))

    mcp_manager = get_mcp_client_manager()
    # Flip the built-in workiq entry into its OAuth-protected preview shape.
    mcp_manager.configure_workiq_preview(True, auth_provider=object())  # type: ignore[arg-type]
    try:
        await _set_mcp_enabled({"workiq": True, "precursor": True})
        configs, oauth_expiry, auth_required = await AgentManager()._catalog_mcp_configs()

        # Skipped for lack of creds: absent from configs, present in auth_required.
        assert "workiq" not in configs
        assert auth_required == ["workiq"]
        assert oauth_expiry is None
    finally:
        mcp_manager.configure_workiq_preview(False, auth_provider=None)
        await _set_mcp_enabled({})


async def test_announce_auth_required_dedupes_and_resets(monkeypatch) -> None:
    """A held session announces a server once, then re-fires after it recovers."""
    mgr = AgentManager()
    emitted: list[tuple[int, AgentEvent]] = []

    async def _record(agent_id: int, event: AgentEvent) -> None:
        emitted.append((agent_id, event))

    monkeypatch.setattr(mgr, "_emit_synthetic", _record)

    await mgr._announce_auth_required(7, ["workiq"])
    await mgr._announce_auth_required(7, ["workiq"])  # still blocked → no repeat

    assert len(emitted) == 1
    agent_id, event = emitted[0]
    assert agent_id == 7
    assert event.kind == "mcp_auth_required"
    assert event.tool_name == "workiq"
    assert event.data == {"server": "workiq"}
    assert "WorkIQ" in (event.text or "")

    # Server authenticated (no longer blocked) → announced set resets.
    await mgr._announce_auth_required(7, [])
    assert len(emitted) == 1

    # A later lapse prompts again rather than staying silent.
    await mgr._announce_auth_required(7, ["workiq"])
    assert len(emitted) == 2


async def test_auth_server_from_failed_tool(monkeypatch) -> None:
    """Only a real WorkIQ auth failure maps a tool error to a sign-in prompt."""
    from precursor.backend.services.mcp import workiq_preview as wp

    mgr = AgentManager()

    async def _no_creds(_profile: object = None) -> None:
        return None

    async def _preview_on() -> bool:
        return True

    monkeypatch.setattr(wp, "resolve_workiq_bearer_token", _no_creds)
    monkeypatch.setattr(wp, "resolve_workiq_preview", _preview_on)

    def _tool(status: str, server: str | None) -> AgentEvent:
        data: dict[str, Any] | None = {"server_name": server} if server else None
        return AgentEvent(kind="tool_result", tool_status=status, data=data)

    # Errored workiq tool with no creds → prompt.
    assert await mgr._auth_server_from_failed_tool(_tool("error", "workiq")) == "workiq"
    # A non-error result never prompts.
    assert await mgr._auth_server_from_failed_tool(_tool("done", "workiq")) is None
    # A different server's failure is not WorkIQ's problem.
    assert await mgr._auth_server_from_failed_tool(_tool("error", "github")) is None

    # Preview off → WorkIQ is local stdio with no OAuth; a tool error must never
    # surface a sign-in prompt the user can't act on.
    async def _preview_off() -> bool:
        return False

    monkeypatch.setattr(wp, "resolve_workiq_preview", _preview_off)
    assert await mgr._auth_server_from_failed_tool(_tool("error", "workiq")) is None

    # Creds are actually present → a workiq error is some other fault, no prompt.
    monkeypatch.setattr(wp, "resolve_workiq_preview", _preview_on)

    async def _has_creds(_profile: object = None) -> tuple[str, None]:
        return "token", None

    monkeypatch.setattr(wp, "resolve_workiq_bearer_token", _has_creds)
    assert await mgr._auth_server_from_failed_tool(_tool("error", "workiq")) is None


async def _set_setting(key: str, value: str | None) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AppSetting

    async with SessionLocal() as session:
        row = await session.get(AppSetting, key)
        if value is None:
            if row is not None:
                await session.delete(row)
        elif row is None:
            session.add(AppSetting(key=key, value=value))
        else:
            row.value = value
        await session.commit()


async def test_auth_skipped_stamps_track_the_stored_credential() -> None:
    """The stamp changes only when the credential behind a server changes.

    This is what makes rebuilding a tool-less session loop-free: a rebuild that
    still can't authenticate re-records the same stamps, so nothing fires again
    until real tokens land.
    """
    from precursor.backend.services.mcp.oauth_registry import credential_key

    with TestClient(create_app()):
        pass

    mgr = AgentManager()
    key = credential_key("workiq")

    assert await mgr._auth_skipped_stamps([]) == frozenset()

    await _set_setting(key, None)
    absent = await mgr._auth_skipped_stamps(["workiq"])
    try:
        # An absent credential still stamps, so signing *in* is a detectable change.
        assert {name for name, _ in absent} == {"workiq"}
        assert await mgr._auth_skipped_stamps(["workiq"]) == absent  # stable

        await _set_setting(key, '{"access_token": "one"}')
        signed_in = await mgr._auth_skipped_stamps(["workiq"])
        assert signed_in != absent

        await _set_setting(key, '{"access_token": "two"}')
        assert await mgr._auth_skipped_stamps(["workiq"]) != signed_in

        # Never the credential itself — only a digest of it.
        assert all("two" not in stamp for _, stamp in await mgr._auth_skipped_stamps(["workiq"]))
    finally:
        await _set_setting(key, None)


async def test_auth_skipped_stamps_share_one_credential() -> None:
    """Servers behind a single sign-in stamp identically — one thing to fix."""
    from precursor.backend.services.mcp.agent365 import AGENT365_SERVERS

    if len(AGENT365_SERVERS) < 2:  # pragma: no cover - guards a config change
        return

    with TestClient(create_app()):
        pass

    names = [spec.name for spec in AGENT365_SERVERS[:2]]
    stamps = await AgentManager()._auth_skipped_stamps(names)
    assert {name for name, _ in stamps} == set(names)
    assert len({stamp for _, stamp in stamps}) == 1


def _agent(mcp_servers: str | None) -> Any:
    from precursor.backend.models import AgentSession

    return AgentSession(id=1, title="t", mcp_servers=mcp_servers)


def test_blocked_on_missing_auth_only_for_an_explicit_scope() -> None:
    """Only a server the agent *named* blocks the turn."""
    live = _LiveSession(sdk_session=object(), mcp_auth_skipped=frozenset({("workiq", "abc")}))

    # No scope at all → the agent asked for the whole catalogue, not this server.
    assert AgentManager._blocked_on_missing_auth(_agent(None), live) == []
    assert AgentManager._blocked_on_missing_auth(_agent(""), live) == []
    # Scoped elsewhere → unaffected by WorkIQ's lapsed sign-in.
    assert AgentManager._blocked_on_missing_auth(_agent("github"), live) == []
    # Scoped to it → blocked, reported by human-facing label.
    blocked = AgentManager._blocked_on_missing_auth(_agent("workiq,github"), live)
    assert blocked and "workiq" not in blocked  # the label, not the raw name

    # Nothing was skipped → nothing blocks, however the agent is scoped.
    attached = _LiveSession(sdk_session=object())
    assert AgentManager._blocked_on_missing_auth(_agent("workiq"), attached) == []


def test_blocked_on_missing_auth_collapses_shared_credential() -> None:
    """A pair sharing one sign-in reads as a single thing to re-authenticate."""
    from precursor.backend.services.mcp.agent365 import AGENT365_SERVERS

    if len(AGENT365_SERVERS) < 2:  # pragma: no cover - guards a config change
        return

    names = [spec.name for spec in AGENT365_SERVERS[:2]]
    live = _LiveSession(
        sdk_session=object(),
        mcp_auth_skipped=frozenset((name, "abc") for name in names),
    )
    assert len(AgentManager._blocked_on_missing_auth(_agent(",".join(names)), live)) == 1


async def test_block_turn_parks_blocked_and_advances_the_workflow(monkeypatch) -> None:
    """The turn is parked ``blocked`` — which is what pauses the run.

    ``blocked`` (rather than the ``idle`` a tool-less agent would otherwise
    reach) is the status ``workflow._advance_one`` treats as "pause and ask",
    instead of recording the step as completed.
    """
    mgr = AgentManager()
    patched: dict[str, Any] = {}
    published: list[int] = []
    advanced: list[int] = []

    async def _patch(agent_id: int, **fields: Any) -> None:
        patched.update({"agent_id": agent_id, **fields})

    async def _publish(agent_id: int) -> None:
        published.append(agent_id)

    async def _advance(agent_id: int) -> None:
        advanced.append(agent_id)

    enqueued: list[Any] = []

    monkeypatch.setattr(mgr, "_patch", _patch)
    monkeypatch.setattr(mgr, "_publish", _publish)
    monkeypatch.setattr(mgr, "_advance_workflows", _advance)
    monkeypatch.setattr(mgr, "enqueue", enqueued.append)

    await mgr._block_turn(7, ["WorkIQ"])

    assert patched["agent_id"] == 7
    assert patched["status"] == "blocked"
    assert patched["error"] is None
    assert "WorkIQ" in patched["blocked_question"]
    assert published == [7]

    # The advance is deferred to the manager's queue (never awaited inline, which
    # would re-enter the lock this runs under); run it to prove that's what it is.
    assert len(enqueued) == 1
    await enqueued[0]
    assert advanced == [7]
