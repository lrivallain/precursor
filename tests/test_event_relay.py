"""Cross-process event relay: a stdio MCP child's writes must reach the SPA.

The built-in ``precursor`` MCP server runs as a subprocess that shares the app's
database but not its in-memory event bus. Writes made there (``append_note``,
``post_message``, reminders) landed in the DB while their ``message.changed``
event went onto the child's own empty bus, so no browser ever heard about them
and the note looked like it had silently failed. These tests pin the relay that
carries those events back to the app process.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.services import events as ev


@pytest.fixture(autouse=True)
def _restore_process_role():
    """Tests flip the module-level "am I the app" flag; put it back."""
    original = ev.is_app_process()
    yield
    ev._is_app_process = original


def test_app_process_never_relays(monkeypatch: pytest.MonkeyPatch) -> None:
    """The app is the bus origin; relaying to itself would loop."""
    monkeypatch.setenv(ev.RELAY_URL_ENV, "http://127.0.0.1:8000/api/events/publish")
    monkeypatch.setenv(ev.RELAY_TOKEN_ENV, "tok")
    ev.mark_app_process()
    assert ev._relay_target() is None


def test_child_relays_when_equipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ev.RELAY_URL_ENV, "http://127.0.0.1:8000/api/events/publish")
    monkeypatch.setenv(ev.RELAY_TOKEN_ENV, "tok")
    ev._is_app_process = False
    assert ev._relay_target() == (
        "http://127.0.0.1:8000/api/events/publish",
        "tok",
    )


def test_child_without_a_token_stays_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """An externally launched MCP host has no parent to notify."""
    monkeypatch.setenv(ev.RELAY_URL_ENV, "http://127.0.0.1:8000/api/events/publish")
    monkeypatch.delenv(ev.RELAY_TOKEN_ENV, raising=False)
    ev._is_app_process = False
    assert ev._relay_target() is None


def test_relay_child_env_dials_loopback_for_a_wildcard_bind() -> None:
    """0.0.0.0 is a bind, not a connectable address."""
    env = ev.relay_child_env(host="0.0.0.0", port=9000, token="tok")
    assert env[ev.RELAY_URL_ENV] == "http://127.0.0.1:9000/api/events/publish"
    assert env[ev.RELAY_TOKEN_ENV] == "tok"


def test_relay_child_env_brackets_an_ipv6_bind() -> None:
    env = ev.relay_child_env(host="::1", port=9000, token="tok")
    assert env[ev.RELAY_URL_ENV] == "http://[::1]:9000/api/events/publish"


async def test_relay_swallows_a_dead_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The write is already committed; the tool must not fail on a nudge."""
    # Port 1 is reserved and never listening.
    monkeypatch.setenv(ev.RELAY_URL_ENV, "http://127.0.0.1:1/api/events/publish")
    monkeypatch.setenv(ev.RELAY_TOKEN_ENV, "tok")
    ev._is_app_process = False
    await ev.get_bus().publish({"type": "message.changed", "topic_id": 1})


async def test_append_note_in_a_child_relays_message_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported bug, end to end.

    ``append_note`` runs in the stdio child, so its ``message.changed`` used to
    die on that process's empty bus and the note stayed invisible in the UI
    until a manual reload. Assert the child now hands the event to the app.
    """
    import httpx

    from precursor.backend.db import init_db
    from precursor.backend.models import AppSetting, Topic
    from precursor.backend.services.collections import resolve_collection_id
    from precursor.backend.services.mcp import precursor_server as ps
    from precursor.backend.services.slugs import allocate_unique_slug, slugify

    # Build the app (not its lifespan) purely for the side effect of populating
    # the relay env, then bring the schema up on this test's own loop.
    create_app()
    await init_db()

    async with SessionLocal() as session:
        row = await session.get(AppSetting, "mcp_expose")
        # Restored in the finally below: the suite shares one DB, and leaving
        # a section switched on makes the "defaults are all off" test fail
        # depending on file order.
        previous = None if row is None else row.value
        if row is None:
            session.add(AppSetting(key="mcp_expose", value='{"notes": true}'))
        else:
            row.value = '{"notes": true}'
        topic = Topic(
            title="Relayed note",
            slug=await allocate_unique_slug(session, slugify("Relayed note"), Topic),
            collection_id=await resolve_collection_id(session, None),
        )
        session.add(topic)
        await session.commit()
        topic_id = topic.id

    sent: list[tuple[str, dict, dict]] = []

    class _Client:
        def __init__(self, *a, **kw) -> None: ...

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *exc) -> None: ...

        async def post(self, url, *, json, headers):  # type: ignore[no-untyped-def]
            sent.append((url, json, headers))

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setenv(ev.RELAY_URL_ENV, "http://127.0.0.1:9000/api/events/publish")
    monkeypatch.setenv(ev.RELAY_TOKEN_ENV, "child-token")
    ev._is_app_process = False

    try:
        result = await ps.append_note(topic_id, "Filed from the MCP child")
    finally:
        async with SessionLocal() as session:
            row = await session.get(AppSetting, "mcp_expose")
            if row is not None:
                if previous is None:
                    await session.delete(row)
                else:
                    row.value = previous
                await session.commit()
    assert result["posted"] is True

    url, payload, headers = next(
        entry for entry in sent if entry[1].get("type") == "message.changed"
    )
    assert url == "http://127.0.0.1:9000/api/events/publish"
    assert payload["topic_id"] == topic_id
    assert headers[ev.RELAY_TOKEN_HEADER] == "child-token"


# --------------------------------------------------------------------------
# POST /api/events/publish — the app-side republish endpoint
# --------------------------------------------------------------------------


def test_publish_requires_the_token() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/api/events/publish", json={"type": "message.changed", "topic_id": 1})
        assert r.status_code == 403
        r = client.post(
            "/api/events/publish",
            json={"type": "message.changed", "topic_id": 1},
            headers={ev.RELAY_TOKEN_HEADER: "wrong"},
        )
        assert r.status_code == 403


def test_publish_rejects_a_non_relayable_type() -> None:
    """Only data-refresh signals. ``mcp.auth_url`` would steer a popup."""
    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/events/publish",
            json={"type": "mcp.auth_url"},
            headers={ev.RELAY_TOKEN_HEADER: _token()},
        )
        assert r.status_code == 400


async def test_publish_lands_on_the_bus_every_window_listens_to() -> None:
    """End to end: the relayed event reaches the bus the SSE endpoint serves."""
    import httpx

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with (
        ev.get_bus().subscribe() as q,
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        r = await client.post(
            "/api/events/publish",
            json={"type": "message.changed", "topic_id": 4242},
            headers={ev.RELAY_TOKEN_HEADER: _token()},
        )
        assert r.status_code == 204
        evt = await asyncio.wait_for(q.get(), timeout=2)

    assert evt["type"] == "message.changed"
    assert evt["topic_id"] == 4242
    # Broadcast, not echo-suppressed: the window whose chat turn triggered the
    # tool is exactly the one that must re-render the new note.
    assert evt["client_id"] is None


def _token() -> str:
    return os.environ[ev.RELAY_TOKEN_ENV]
