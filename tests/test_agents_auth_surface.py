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
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.schemas.agent import AgentEvent
from precursor.backend.services.agents.manager import AgentManager


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

    async def _no_creds() -> None:
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

    async def _has_creds() -> tuple[str, None]:
        return "token", None

    monkeypatch.setattr(wp, "resolve_workiq_bearer_token", _has_creds)
    assert await mgr._auth_server_from_failed_tool(_tool("error", "workiq")) is None
