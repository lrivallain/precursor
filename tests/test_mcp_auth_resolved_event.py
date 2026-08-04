"""The cross-window ``mcp.auth_resolved`` broadcast.

When one window renews a WorkIQ sign-in, every *other* window still showing the
``McpAuthBanner`` needs to hear that the credentials are fresh so it drops the
banner without a reload. That signal is a single event-bus broadcast; this
covers that the publisher emits it with the server. (That the reauthenticate
endpoint fires it on a successful sign-in is covered in ``test_workiq_preview``,
alongside the other endpoint tests that share its preview-mode fixture.)
"""

from __future__ import annotations

import asyncio

import pytest

from precursor.backend.services import events


async def test_publish_mcp_auth_resolved_broadcasts_server() -> None:
    async with events.get_bus().subscribe() as q:
        await events.publish_mcp_auth_resolved("workiq")
        evt = await asyncio.wait_for(q.get(), timeout=2)

    assert evt["type"] == "mcp.auth_resolved"
    assert evt["server"] == "workiq"


async def test_renew_stale_sibling_credentials_chains_the_other_entra_client(
    monkeypatch,
) -> None:
    """A fresh sign-in re-announces the *other* credential while SSO is hot.

    Precursor's WorkIQ servers span two Entra clients, so one sign-in never
    covers both. Re-emitting ``mcp.auth_required`` for a sibling still parked in
    ``needs_auth`` is what lets the SPA renew it hands-free instead of prompting
    the user a second time minutes later.
    """
    import types

    from precursor.backend.routers import mcp as mcp_router
    from precursor.backend.services.mcp.agent365 import build_profile
    from precursor.backend.services.mcp.workiq_preview import PREVIEW_PROFILE

    teams = build_profile("workiq-teams", "00000000-0000-0000-0000-000000000000")

    async def _profile_for(name: str):
        return teams if name == "workiq-teams" else None

    async def _preview_on() -> bool:
        return True

    monkeypatch.setattr(
        "precursor.backend.services.mcp.agent365.profile_for", _profile_for, raising=False
    )
    monkeypatch.setattr(
        "precursor.backend.services.mcp.workiq_preview.resolve_workiq_preview",
        _preview_on,
        raising=False,
    )

    manager = types.SimpleNamespace(
        get=lambda name: (
            types.SimpleNamespace(state="needs_auth") if name == "workiq-teams" else None
        )
    )
    enabled = {"workiq": True, "workiq-teams": True}

    async with events.get_bus().subscribe() as q:
        await mcp_router._renew_stale_sibling_credentials(PREVIEW_PROFILE, manager, enabled)
        evt = await asyncio.wait_for(q.get(), timeout=2)

    assert evt["type"] == "mcp.auth_required"
    assert evt["server"] == "workiq-teams"


async def test_renew_stale_sibling_credentials_skips_healthy_and_same_credential(
    monkeypatch,
) -> None:
    """No prompt for a sibling that is fine, disabled, or already covered."""
    import types

    from precursor.backend.routers import mcp as mcp_router
    from precursor.backend.services.mcp.agent365 import build_profile
    from precursor.backend.services.mcp.workiq_preview import PREVIEW_PROFILE

    teams = build_profile("workiq-teams", "00000000-0000-0000-0000-000000000000")

    async def _profile_for(name: str):
        return teams if name == "workiq-teams" else None

    async def _preview_on() -> bool:
        return True

    monkeypatch.setattr(
        "precursor.backend.services.mcp.agent365.profile_for", _profile_for, raising=False
    )
    monkeypatch.setattr(
        "precursor.backend.services.mcp.workiq_preview.resolve_workiq_preview",
        _preview_on,
        raising=False,
    )

    # Sibling is connected, not parked in needs_auth → nothing to renew.
    manager = types.SimpleNamespace(get=lambda _name: types.SimpleNamespace(state="ready"))

    async with events.get_bus().subscribe() as q:
        await mcp_router._renew_stale_sibling_credentials(
            PREVIEW_PROFILE, manager, {"workiq": True, "workiq-teams": True}
        )
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(q.get(), timeout=0.2)

    # Stale but disabled → still nothing (the user isn't using that server).
    stale = types.SimpleNamespace(get=lambda _name: types.SimpleNamespace(state="needs_auth"))

    async with events.get_bus().subscribe() as q:
        await mcp_router._renew_stale_sibling_credentials(
            PREVIEW_PROFILE, stale, {"workiq": True, "workiq-teams": False}
        )
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(q.get(), timeout=0.2)
