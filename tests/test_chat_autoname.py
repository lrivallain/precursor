"""Auto-naming a chat from its first message, and the /suggest-name command."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.routers import chat_messages
from precursor.backend.services import chat_autoname
from precursor.backend.services.chat_autoname import MAX_TITLE_CHARS, sanitize_title
from precursor.backend.services.llm.base import TextDeltaEvent, TurnDoneEvent, UsageEvent

# --- sanitize_title --------------------------------------------------------
#
# The sanitiser is the whole safety net: it decides what a model is allowed to
# write into a title, and returning "" is what keeps the existing title when the
# model misbehaves. It's pure, so it's tested directly rather than through a stub.


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Fixing the login redirect", "Fixing the login redirect"),
        # Wrappers the model adds despite being told not to.
        ('"Fixing the login redirect"', "Fixing the login redirect"),
        ("'Fixing the login redirect'", "Fixing the login redirect"),
        ("`Fixing the login redirect`", "Fixing the login redirect"),
        ("**Fixing the login redirect**", "Fixing the login redirect"),
        ('**"Fixing the login redirect"**', "Fixing the login redirect"),
        ("“Fixing the login redirect”", "Fixing the login redirect"),
        ("«Fixing the login redirect»", "Fixing the login redirect"),
        # A leading label, in a few languages.
        ("Title: Fixing the login redirect", "Fixing the login redirect"),
        ("Titre : Correction du login", "Correction du login"),
        # Trailing sentence punctuation is noise on a title...
        ("Fixing the login redirect.", "Fixing the login redirect"),
        # ...but a question mark carries meaning and is kept.
        ("Why does login redirect?", "Why does login redirect?"),
        # Only the first non-empty line is the title.
        ("Fixing the login redirect\n\nLet me know if…", "Fixing the login redirect"),
        ("\n\nFixing the login redirect", "Fixing the login redirect"),
        # Internal whitespace collapses.
        ("Fixing   the\tlogin redirect", "Fixing the login redirect"),
        # Nothing usable => keep the existing title.
        ("", ""),
        ("   ", ""),
        ('""', ""),
        ("...", ""),
    ],
)
def test_sanitize_title_normalises_model_output(raw: str, expected: str) -> None:
    assert sanitize_title(raw) == expected


def test_sanitize_title_truncates_on_a_word_boundary() -> None:
    long = "Investigating why the nightly deployment pipeline keeps timing out"
    out = sanitize_title(long)
    assert len(out) <= MAX_TITLE_CHARS + 1  # the ellipsis may sit one past the cap
    assert out.endswith("…")
    # Truncation must not leave a cut-in-half word before the ellipsis.
    assert long.startswith(out[:-1])
    assert out[:-1].strip() == out[:-1]


def test_sanitize_title_rejects_an_answer_instead_of_a_title() -> None:
    """A model that answers the question rather than naming it changes nothing."""
    prose = (
        "To fix the login redirect you should first check the callback URL "
        "registered with the identity provider, then confirm the session cookie "
        "is being set with the correct SameSite attribute before retrying."
    )
    assert sanitize_title(prose) == ""


# --- the autoname_pending guard -------------------------------------------


def test_autoname_flag_defaults_off_and_is_opt_in() -> None:
    """Only a client that says its title is a placeholder licenses a rename."""
    app = create_app()
    with TestClient(app) as client:
        plain = client.post("/api/chats", json={"title": "Quarterly planning"}).json()
        auto = client.post("/api/chats", json={"title": "New chat", "autoname": True}).json()

        # The flag is server-side bookkeeping, not part of the read model.
        assert "autoname_pending" not in plain
        assert client.get(f"/api/chats/{auto['id']}").status_code == 200


def test_manual_rename_clears_the_pending_autoname(monkeypatch: pytest.MonkeyPatch) -> None:
    """A title the user typed must survive a naming pass scheduled before it."""
    scheduled: list[int] = []
    # The router imports the symbol directly, so that binding is what to patch.
    monkeypatch.setattr(
        chat_messages, "schedule_autoname", lambda chat_id, *, prompt: scheduled.append(chat_id)
    )

    app = create_app()
    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"title": "New chat", "autoname": True}).json()
        client.patch(f"/api/chats/{chat['id']}", json={"title": "Ops handover"})

        # With the flag cleared, a later turn no longer schedules naming — and
        # an in-flight pass re-reads the same flag before writing.
        client.post(f"/api/chats/{chat['id']}/messages/stream", json={"content": "hello"})

    assert scheduled == []


def test_first_turn_schedules_naming_with_the_expanded_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skill invocation names the chat after what it asked, not `/to-en …`."""
    seen: list[str] = []
    monkeypatch.setattr(
        chat_messages, "schedule_autoname", lambda chat_id, *, prompt: seen.append(prompt)
    )

    app = create_app()
    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"title": "New chat", "autoname": True}).json()
        client.post(
            f"/api/chats/{chat['id']}/messages/stream",
            json={"content": "/to-en bravo", "prompt_override": "Translate 'bravo' to English"},
        )

    assert seen == ["Translate 'bravo' to English"]


# --- /suggest-name ---------------------------------------------------------


class _TitleProvider:
    """Returns a fixed title, as a naming call would."""

    name = "fake"

    def __init__(self, text: str) -> None:
        self._text = text

    async def stream_chat_with_tools(self, **_kwargs):  # type: ignore[no-untyped-def]
        yield TextDeltaEvent(content=self._text)
        yield UsageEvent(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        yield TurnDoneEvent(finish_reason="stop")

    async def list_models(self):  # type: ignore[no-untyped-def]
        return []


def _stub_provider_factory(text: str):  # type: ignore[no-untyped-def]
    async def _get(_session):  # type: ignore[no-untyped-def]
        return _TitleProvider(text)

    return _get


def _stub_provider(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr(chat_autoname, "get_llm_provider", _stub_provider_factory(text))


def test_suggest_name_renames_a_chat_without_touching_its_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_provider(monkeypatch, '"Debugging the login redirect."')

    app = create_app()
    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"title": "New chat", "autoname": True}).json()
        slug = chat["slug"]
        # Naming reads the transcript, so give it one.
        client.post(
            f"/api/chats/{chat['id']}/messages/notes/append",
            json={"text": "The login page keeps redirecting in a loop."},
        )

        r = client.post(f"/api/chats/{chat['id']}/messages/suggest-name")
        assert r.status_code == 200
        assert r.json()["title"] == "Debugging the login redirect"

        after = client.get(f"/api/chats/{chat['id']}").json()
        assert after["title"] == "Debugging the login redirect"
        # The slug is what deep links resolve on, so a rename must not move it.
        assert after["slug"] == slug
        assert client.get(f"/api/chats/by-slug/{slug}").json()["id"] == chat["id"]


def test_suggest_name_renames_a_topic_without_touching_its_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_provider(monkeypatch, "Migrating to Postgres")

    app = create_app()
    with TestClient(app) as client:
        topic = client.post("/api/topics", json={"title": "Untitled work"}).json()
        slug = topic["slug"]
        client.post(
            f"/api/topics/{topic['id']}/commands/notes/append",
            json={"text": "We should move the store off SQLite."},
        )

        r = client.post(f"/api/topics/{topic['id']}/commands/suggest-name")
        assert r.status_code == 200
        assert r.json()["title"] == "Migrating to Postgres"

        after = client.get(f"/api/topics/{topic['id']}").json()
        assert after["title"] == "Migrating to Postgres"
        assert after["slug"] == slug


def test_suggest_name_keeps_the_title_on_an_empty_conversation() -> None:
    """Nothing has been said yet, so there is nothing to name it after."""
    app = create_app()
    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"title": "Quarterly planning"}).json()

        r = client.post(f"/api/chats/{chat['id']}/messages/suggest-name")
        assert r.status_code == 200
        assert r.json()["title"] == ""
        assert client.get(f"/api/chats/{chat['id']}").json()["title"] == "Quarterly planning"


def test_suggest_name_keeps_the_title_when_the_model_returns_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_provider(monkeypatch, "   ")

    app = create_app()
    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"title": "Quarterly planning"}).json()
        client.post(f"/api/chats/{chat['id']}/messages/notes/append", json={"text": "Budget?"})

        r = client.post(f"/api/chats/{chat['id']}/messages/suggest-name")
        assert r.status_code == 200
        assert r.json()["title"] == ""
        assert client.get(f"/api/chats/{chat['id']}").json()["title"] == "Quarterly planning"


def test_suggest_name_survives_a_provider_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Naming is advisory — a dead provider must not 500 the command."""

    async def _boom(_session):  # type: ignore[no-untyped-def]
        raise RuntimeError("provider is down")

    monkeypatch.setattr(chat_autoname, "get_llm_provider", _boom)

    app = create_app()
    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"title": "Quarterly planning"}).json()
        client.post(f"/api/chats/{chat['id']}/messages/notes/append", json={"text": "Budget?"})

        r = client.post(f"/api/chats/{chat['id']}/messages/suggest-name")
        assert r.status_code == 200
        assert r.json()["title"] == ""
        assert client.get(f"/api/chats/{chat['id']}").json()["title"] == "Quarterly planning"


def test_suggest_name_is_404_for_an_unknown_conversation() -> None:
    app = create_app()
    with TestClient(app) as client:
        assert client.post("/api/chats/9999/messages/suggest-name").status_code == 404
        assert client.post("/api/topics/9999/commands/suggest-name").status_code == 404


# --- the naming pass itself ------------------------------------------------


def test_autoname_replaces_the_placeholder_and_clears_the_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_provider(monkeypatch, "Fixing the login redirect")

    app = create_app()
    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"title": "New chat", "autoname": True}).json()

        asyncio.run(
            chat_autoname._autoname_chat(chat["id"], prompt="Why does my login redirect loop?")
        )

        assert client.get(f"/api/chats/{chat['id']}").json()["title"] == "Fixing the login redirect"

        # The flag is spent: a second pass (a retry, a re-scheduled turn) must
        # not overwrite the name that is now on the chat.
        _stub_provider(monkeypatch, "Something else entirely")
        asyncio.run(chat_autoname._autoname_chat(chat["id"], prompt="anything"))
        assert client.get(f"/api/chats/{chat['id']}").json()["title"] == "Fixing the login redirect"


def test_autoname_leaves_an_unflagged_chat_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_provider(monkeypatch, "Fixing the login redirect")

    app = create_app()
    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"title": "Quarterly planning"}).json()

        asyncio.run(chat_autoname._autoname_chat(chat["id"], prompt="hello"))

        assert client.get(f"/api/chats/{chat['id']}").json()["title"] == "Quarterly planning"


def test_autoname_respects_the_settings_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_provider(monkeypatch, "Fixing the login redirect")

    app = create_app()
    with TestClient(app) as client:
        client.put("/api/settings", json={"chat_autoname_enabled": False})
        chat = client.post("/api/chats", json={"title": "New chat", "autoname": True}).json()

        asyncio.run(chat_autoname._autoname_chat(chat["id"], prompt="Why does login loop?"))
        assert client.get(f"/api/chats/{chat['id']}").json()["title"] == "New chat"

        # The flag is deliberately left set, so turning the setting back on
        # still names the conversations created while it was off.
        client.put("/api/settings", json={"chat_autoname_enabled": True})
        asyncio.run(chat_autoname._autoname_chat(chat["id"], prompt="Why does login loop?"))
        assert client.get(f"/api/chats/{chat['id']}").json()["title"] == "Fixing the login redirect"


def test_autoname_never_raises_when_the_provider_dies(monkeypatch: pytest.MonkeyPatch) -> None:
    """It rides along with a real turn, so it must fail silently."""

    async def _boom(_session):  # type: ignore[no-untyped-def]
        raise RuntimeError("provider is down")

    monkeypatch.setattr(chat_autoname, "get_llm_provider", _boom)

    app = create_app()
    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"title": "New chat", "autoname": True}).json()

        asyncio.run(chat_autoname._autoname_chat(chat["id"], prompt="hello"))

        assert client.get(f"/api/chats/{chat['id']}").json()["title"] == "New chat"


def test_a_failed_naming_pass_is_retried_on_the_next_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider blip must not condemn the chat to its placeholder for good."""

    async def _boom(_session):  # type: ignore[no-untyped-def]
        raise RuntimeError("provider is down")

    monkeypatch.setattr(chat_autoname, "get_llm_provider", _boom)

    app = create_app()
    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"title": "New chat", "autoname": True}).json()
        asyncio.run(chat_autoname._autoname_chat(chat["id"], prompt="Why does login loop?"))
        assert client.get(f"/api/chats/{chat['id']}").json()["title"] == "New chat"

        # The flag survived, so the next attempt still names it.
        _stub_provider(monkeypatch, "Fixing the login redirect")
        asyncio.run(chat_autoname._autoname_chat(chat["id"], prompt="Why does login loop?"))
        assert client.get(f"/api/chats/{chat['id']}").json()["title"] == "Fixing the login redirect"


def test_streaming_the_first_turn_renames_the_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: the real detached task fires from the stream endpoint.

    Covers what the stubbed tests above can't — that the fire-and-forget task is
    actually scheduled, survives to completion (it isn't garbage-collected), and
    commits through its own session after the request's one is gone.
    """
    _stub_provider(monkeypatch, "Fixing the login redirect")
    # Stub the turn's own provider too, so the test never reaches a real API.
    # Naming resolves its provider independently; only the title is under test.
    monkeypatch.setattr(chat_messages, "get_llm_provider", _stub_provider_factory("ok"))

    app = create_app()
    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"title": "New chat", "autoname": True}).json()

        with client.stream(
            "POST",
            f"/api/chats/{chat['id']}/messages/stream",
            json={"content": "Why does my login redirect loop?"},
        ) as r:
            for _ in r.iter_lines():
                pass

        # The task is detached, so give the loop a moment to finish it.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            title = client.get(f"/api/chats/{chat['id']}").json()["title"]
            if title != "New chat":
                break
            time.sleep(0.05)

        assert title == "Fixing the login redirect"


def test_shutdown_cancels_detached_autoname_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A TestClient/app shutdown must not leave naming DB work running."""
    started = threading.Event()
    cancelled = threading.Event()

    async def _parked_autoname(_chat_id: int, *, prompt: str) -> None:
        assert prompt == "Why does my login redirect loop?"
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(chat_autoname, "_autoname_chat", _parked_autoname)
    monkeypatch.setattr(chat_messages, "get_llm_provider", _stub_provider_factory("ok"))

    app = create_app()
    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"title": "New chat", "autoname": True}).json()

        with client.stream(
            "POST",
            f"/api/chats/{chat['id']}/messages/stream",
            json={"content": "Why does my login redirect loop?"},
        ) as r:
            for _ in r.iter_lines():
                pass

        assert started.wait(2.0)
        assert any(not task.done() for task in chat_autoname._pending)

    assert cancelled.wait(2.0)
    assert not chat_autoname._pending
