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


async def test_adopt_shared_credential_flips_every_server_on_that_token(
    monkeypatch,
) -> None:
    """One sign-in clears the whole Agent 365 family.

    The endpoints share a single Entra token, so a sibling left parked in
    ``needs_auth`` was asking for a sign-in it already had — the trap that turned
    six servers into six sign-ins for two credentials.
    """
    import types

    from precursor.backend.routers import mcp as mcp_router
    from precursor.backend.services.mcp.agent365 import AGENT365_SERVERS, build_profile

    tenant = "00000000-0000-0000-0000-000000000000"
    family = [spec.name for spec in AGENT365_SERVERS]

    async def _profile_for(name: str):
        return build_profile(name, tenant) if name in family else None

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

    configured: list[str] = []
    retired: list[str] = []
    probed: list[str] = []

    class _Manager:
        def get(self, name: str) -> object:
            return types.SimpleNamespace(state="needs_auth")

        def configure_agent365(self, name: str, *, url: str, auth_provider: object) -> None:
            configured.append(name)

        async def retire_worker(self, name: str) -> None:
            retired.append(name)

        async def probe(self, name: str, *, github_token: str | None) -> None:
            probed.append(name)

    signed_in = build_profile("workiq-teams", tenant)
    # One sibling is disabled: adopt the credential for it, but don't connect it.
    enabled = {name: name != "workiq-word" for name in family}

    adopted = await mcp_router._adopt_shared_credential(signed_in, _Manager(), enabled, None)

    siblings = [name for name in family if name != "workiq-teams"]
    assert siblings  # the family is more than one server, or this proves nothing
    # The server that just signed in is not re-done, and every other one is.
    assert adopted == siblings
    assert configured == siblings
    assert retired == siblings
    assert probed == [name for name in siblings if name != "workiq-word"]


async def test_adopt_shared_credential_leaves_the_other_entra_client_alone(
    monkeypatch,
) -> None:
    """The WorkIQ preview is alone on its credential, so it adopts nothing.

    Its token is a different Entra client against a different resource — quietly
    marking it authenticated off an Agent 365 sign-in would be a lie.
    """
    import types

    from precursor.backend.routers import mcp as mcp_router
    from precursor.backend.services.mcp.agent365 import build_profile
    from precursor.backend.services.mcp.workiq_preview import PREVIEW_PROFILE

    async def _profile_for(name: str):
        return build_profile(name, "00000000-0000-0000-0000-000000000000")

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

    touched: list[str] = []

    class _Manager:
        def get(self, name: str) -> object:
            return types.SimpleNamespace(state="needs_auth")

        def configure_agent365(self, name: str, *, url: str, auth_provider: object) -> None:
            touched.append(name)

        async def retire_worker(self, name: str) -> None: ...

        async def probe(self, name: str, *, github_token: str | None) -> None: ...

    adopted = await mcp_router._adopt_shared_credential(
        PREVIEW_PROFILE, _Manager(), {"workiq": True}, None
    )

    assert adopted == []
    assert touched == []


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
