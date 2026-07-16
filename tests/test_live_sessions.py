"""Live meeting assistant API tests — session CRUD lifecycle.

Phase 1 covers the session lifecycle only (create / list / get / update /
delete) plus the optional topic link. Transcript ingestion, live analysis, and
summary attachment are exercised in later phases.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app


def test_meeting_session_crud_lifecycle() -> None:
    app = create_app()
    with TestClient(app) as client:
        # Create with no topic and no title — server generates a dated title.
        created = client.post("/api/live", json={})
        assert created.status_code == 201
        body = created.json()
        sid = body["id"]
        assert body["status"] == "active"
        assert body["slug"]
        assert body["title"]
        assert body["topic_id"] is None
        assert body["ended_at"] is None

        # It shows up in the list.
        listing = client.get("/api/live")
        assert listing.status_code == 200
        assert any(s["id"] == sid for s in listing.json())

        # Fetch by id.
        got = client.get(f"/api/live/{sid}")
        assert got.status_code == 200
        assert got.json()["id"] == sid

        # Rename + set language.
        patched = client.patch(
            f"/api/live/{sid}", json={"title": "Sprint sync", "language": "fr-FR"}
        )
        assert patched.status_code == 200
        assert patched.json()["title"] == "Sprint sync"
        assert patched.json()["language"] == "fr-FR"

        # Ending the session stamps ended_at.
        ended = client.patch(f"/api/live/{sid}", json={"status": "ended"})
        assert ended.status_code == 200
        assert ended.json()["status"] == "ended"
        assert ended.json()["ended_at"] is not None

        # Delete.
        deleted = client.delete(f"/api/live/{sid}")
        assert deleted.status_code == 204
        assert client.get(f"/api/live/{sid}").status_code == 404


def test_meeting_session_topic_link_and_validation() -> None:
    app = create_app()
    with TestClient(app) as client:
        topic = client.post("/api/topics", json={"title": "Context topic"})
        assert topic.status_code in (200, 201)
        tid = topic.json()["id"]

        # Attaching an existing topic works.
        created = client.post("/api/live", json={"title": "Kickoff", "topic_id": tid})
        assert created.status_code == 201
        assert created.json()["topic_id"] == tid

        # A non-existent topic is rejected.
        bad = client.post("/api/live", json={"topic_id": 999_999})
        assert bad.status_code == 400

        # Detaching the topic (null) is allowed.
        sid = created.json()["id"]
        detached = client.patch(f"/api/live/{sid}", json={"topic_id": None})
        assert detached.status_code == 200
        assert detached.json()["topic_id"] is None


def test_meeting_session_archive_lifecycle() -> None:
    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "Archivable"}).json()["id"]

        # Archive it: gone from the main list, present in the archived list.
        archived = client.post(f"/api/live/{sid}/archive")
        assert archived.status_code == 200
        assert archived.json()["archived_at"] is not None
        assert all(s["id"] != sid for s in client.get("/api/live").json())
        assert any(s["id"] == sid for s in client.get("/api/live/archived").json())

        # Archiving again is idempotent.
        assert client.post(f"/api/live/{sid}/archive").status_code == 200

        # Restore it: back in the main list, gone from the archived list.
        restored = client.post(f"/api/live/{sid}/unarchive")
        assert restored.status_code == 200
        assert restored.json()["archived_at"] is None
        assert any(s["id"] == sid for s in client.get("/api/live").json())
        assert all(s["id"] != sid for s in client.get("/api/live/archived").json())

        # Unknown ids 404 on both actions.
        assert client.post("/api/live/999999/archive").status_code == 404
        assert client.post("/api/live/999999/unarchive").status_code == 404


def test_meeting_session_slugs_are_unique() -> None:
    app = create_app()
    with TestClient(app) as client:
        a = client.post("/api/live", json={"title": "Standup"})
        b = client.post("/api/live", json={"title": "Standup"})
        assert a.status_code == b.status_code == 201
        assert a.json()["slug"] != b.json()["slug"]


def test_meeting_segments_append_and_list() -> None:
    app = create_app()
    with TestClient(app) as client:
        created = client.post("/api/live", json={"title": "Transcript test"})
        sid = created.json()["id"]
        assert created.json()["started_at"] is None

        # Append two diarized phrases.
        seg1 = client.post(
            f"/api/live/{sid}/segments",
            json={"text": "Bonjour", "speaker_label": "Guest-1", "offset_ms": 0},
        )
        assert seg1.status_code == 201
        assert seg1.json()["speaker_label"] == "Guest-1"
        assert seg1.json()["text"] == "Bonjour"

        client.post(
            f"/api/live/{sid}/segments",
            json={"text": "Salut", "speaker_label": "Guest-2", "offset_ms": 1500},
        )

        # The first segment stamps started_at on the session.
        assert client.get(f"/api/live/{sid}").json()["started_at"] is not None

        listing = client.get(f"/api/live/{sid}/segments")
        assert listing.status_code == 200
        rows = listing.json()
        assert [r["text"] for r in rows] == ["Bonjour", "Salut"]
        assert [r["speaker_label"] for r in rows] == ["Guest-1", "Guest-2"]

        # Deleting the session cascades to its segments (404 on the list).
        assert client.delete(f"/api/live/{sid}").status_code == 204
        assert client.get(f"/api/live/{sid}/segments").status_code == 404


def test_meeting_segments_reject_unknown_session() -> None:
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/live/999999/segments").status_code == 404
        assert client.post("/api/live/999999/segments", json={"text": "x"}).status_code == 404


def test_parse_insights_tolerates_fences_and_junk() -> None:
    from precursor.backend.services.meeting_analysis import _parse_insights

    good = '{"insights": [{"kind": "action_item", "content": "Ship the fix"}]}'
    assert _parse_insights(good)[0] == [("action_item", "Ship the fix")]

    fenced = "```json\n" + good + "\n```"
    assert _parse_insights(fenced)[0] == [("action_item", "Ship the fix")]

    prose = "Here you go:\n" + good + "\nHope that helps!"
    assert _parse_insights(prose)[0] == [("action_item", "Ship the fix")]

    # Unknown kinds are dropped; empty content is dropped.
    mixed = '{"insights": [{"kind":"bogus","content":"x"},{"kind":"risk","content":""}]}'
    assert _parse_insights(mixed)[0] == []

    assert _parse_insights("not json at all")[0] == []


def test_meeting_analyze_persists_snapshot(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import precursor.backend.services.meeting_analysis as analysis
    from precursor.backend.services.llm.base import TextDeltaEvent, UsageEvent

    class _FakeProvider:
        name = "fake"

        async def stream_chat_with_tools(self, **_kwargs):  # type: ignore[no-untyped-def]
            payload = (
                '{"insights": ['
                '{"kind": "decision", "content": "Use Postgres"},'
                '{"kind": "action_item", "content": "Draft the schema"}],'
                '"help": true, "suggestion": "Consider connection pooling"}'
            )
            yield TextDeltaEvent(content=payload)
            yield UsageEvent(prompt_tokens=1, completion_tokens=1, total_tokens=2)

    async def _fake_get_provider(_session, **_kwargs):  # type: ignore[no-untyped-def]
        return _FakeProvider()

    monkeypatch.setattr(analysis, "get_llm_provider", _fake_get_provider)

    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "Analyze"}).json()["id"]
        # No transcript yet => analyze is a no-op.
        empty = client.post(f"/api/live/{sid}/analyze").json()
        assert empty["insights"] == []
        assert empty["suggestion"] == ""

        client.post(f"/api/live/{sid}/segments", json={"text": "Let's use Postgres"})
        result = client.post(f"/api/live/{sid}/analyze")
        assert result.status_code == 200
        body = result.json()
        kinds = {i["kind"] for i in body["insights"]}
        assert kinds == {"decision", "action_item"}
        # The proactive suggestion rides along on the same pass.
        assert body["suggestion"] == "Consider connection pooling"

        # Insights are replaced + de-duplicated: re-analysing the same content
        # swaps in a fresh snapshot without growing the set.
        assert len(client.get(f"/api/live/{sid}/insights").json()) == 2
        assert len(client.post(f"/api/live/{sid}/analyze").json()["insights"]) == 2


def test_meeting_analyze_replaces_across_runs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import precursor.backend.services.meeting_analysis as analysis
    from precursor.backend.services.llm.base import TextDeltaEvent, UsageEvent

    payloads = iter(
        [
            '{"insights": [{"kind": "decision", "content": "Use Postgres"}], "help": false, "suggestion": ""}',
            # A later run surfaces a different insight; it replaces the first.
            '{"insights": [{"kind": "risk", "content": "Tight deadline"}], "help": false, "suggestion": ""}',
            # An empty run must keep the previous snapshot (no blank period).
            '{"insights": [], "help": false, "suggestion": ""}',
        ]
    )

    class _SeqProvider:
        name = "fake"

        async def stream_chat_with_tools(self, **_kwargs):  # type: ignore[no-untyped-def]
            yield TextDeltaEvent(content=next(payloads))
            yield UsageEvent(prompt_tokens=1, completion_tokens=1, total_tokens=2)

    async def _prov(_session, **_kwargs):  # type: ignore[no-untyped-def]
        return _SeqProvider()

    monkeypatch.setattr(analysis, "get_llm_provider", _prov)

    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "Replace"}).json()["id"]
        client.post(f"/api/live/{sid}/segments", json={"text": "hi"})
        first = client.post(f"/api/live/{sid}/analyze").json()["insights"]
        assert {i["content"] for i in first} == {"Use Postgres"}
        second = client.post(f"/api/live/{sid}/analyze").json()["insights"]
        # The new snapshot replaces the earlier one entirely.
        assert {i["content"] for i in second} == {"Tight deadline"}
        third = client.post(f"/api/live/{sid}/analyze").json()["insights"]
        # An empty pass keeps the prior snapshot instead of blanking it.
        assert {i["content"] for i in third} == {"Tight deadline"}


def test_meeting_ask_streams_answer() -> None:
    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "Ask"}).json()["id"]
        client.post(f"/api/live/{sid}/segments", json={"text": "We discussed pricing"})
        with client.stream(
            "POST", f"/api/live/{sid}/ask", json={"question": "What was discussed?"}
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())
        assert "event: token" in body
        assert "event: done" in body


def test_meeting_summary_generate_and_post() -> None:
    app = create_app()
    with TestClient(app) as client:
        topic = client.post("/api/topics", json={"title": "Roadmap"})
        tid = topic.json()["id"]
        sid = client.post("/api/live", json={"title": "Recap", "topic_id": tid}).json()["id"]

        # Nothing recorded yet → 400.
        assert client.post(f"/api/live/{sid}/summary").status_code == 400

        client.post(f"/api/live/{sid}/segments", json={"text": "We shipped the API"})
        gen = client.post(f"/api/live/{sid}/summary")
        assert gen.status_code == 200
        assert isinstance(gen.json()["summary"], str) and gen.json()["summary"]

        # The recap is persisted on the session so a reopen shows it without
        # regenerating; it isn't posted yet.
        reopened = client.get(f"/api/live/{sid}").json()
        assert reopened["summary"] == gen.json()["summary"]
        assert reopened["summary_posted_at"] is None
        assert reopened["summary_posted_topic_id"] is None

        # Post the summary into the linked topic → a new assistant message.
        posted = client.post(
            f"/api/live/{sid}/summary/post", json={"summary": "## Summary\nAll good."}
        )
        assert posted.status_code == 201
        assert posted.json()["topic_id"] == tid
        assert posted.json()["posted_at"]
        msgs = client.get(f"/api/topics/{tid}/messages").json()
        assert any("Meeting summary" in m["content"] for m in msgs)

        # Posting stamps when/where the recap landed and stores the posted text.
        after = client.get(f"/api/live/{sid}").json()
        assert after["summary_posted_at"] is not None
        assert after["summary_posted_topic_id"] == tid
        assert after["summary"] == "## Summary\nAll good."


def test_meeting_summary_post_requires_topic() -> None:
    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "No topic"}).json()["id"]
        client.post(f"/api/live/{sid}/segments", json={"text": "hi"})
        resp = client.post(f"/api/live/{sid}/summary/post", json={"summary": "x"})
        assert resp.status_code == 400


def test_meeting_summary_post_mirrors_to_linked_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the topic carries a GitHub issue, posting also comments there."""
    from precursor.backend.routers import live as live_router

    posted_comments: list[tuple[str, int, str]] = []

    class _FakeClient:
        def __init__(self, *, token: str) -> None:
            self.token = token

        async def aclose(self) -> None:
            return None

        async def add_issue_comment(self, repo: str, number: int, body: str) -> dict[str, str]:
            posted_comments.append((repo, number, body))
            return {"url": f"https://github.com/{repo}/issues/{number}#c1"}

    async def _enabled(_session: object) -> bool:
        return True

    async def _repo(_session: object) -> str:
        return "acme/app"

    async def _token(_session: object) -> str:
        return "tok"

    monkeypatch.setattr(live_router, "GitHubClient", _FakeClient)
    monkeypatch.setattr(live_router, "resolve_issue_associations_enabled", _enabled)
    monkeypatch.setattr(live_router, "resolve_global_github_repo", _repo)
    monkeypatch.setattr(live_router, "resolve_github_token", _token)

    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "Roadmap"}).json()["id"]
        client.patch(
            f"/api/topics/{tid}",
            json={"github_repo": "acme/app", "github_issue_number": 42},
        )
        sid = client.post("/api/live", json={"title": "Recap", "topic_id": tid}).json()["id"]
        client.post(f"/api/live/{sid}/segments", json={"text": "We shipped the API"})

        posted = client.post(
            f"/api/live/{sid}/summary/post", json={"summary": "## Summary\nAll good."}
        )
        assert posted.status_code == 201
        data = posted.json()
        assert data["issue_number"] == 42
        assert data["issue_comment_url"] == "https://github.com/acme/app/issues/42#c1"

        # The comment reached GitHub with the recap body.
        assert len(posted_comments) == 1
        repo, number, body = posted_comments[0]
        assert repo == "acme/app"
        assert number == 42
        assert "Meeting summary" in body


def test_live_enabled_setting_roundtrips() -> None:
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/settings").json()["live_enabled"] is True
        client.put("/api/settings", json={"live_enabled": False})
        assert client.get("/api/settings").json()["live_enabled"] is False


def test_speaker_rename_maps_all_and_clears() -> None:
    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "Diarized"}).json()["id"]
        assert client.get(f"/api/live/{sid}").json()["speaker_names"] == {}

        # Two phrases from the same raw diarization label.
        client.post(f"/api/live/{sid}/segments", json={"text": "Hi", "speaker_label": "Guest-2"})
        client.post(f"/api/live/{sid}/segments", json={"text": "Bye", "speaker_label": "Guest-2"})

        # Rename maps the label for the whole session (segments keep raw label).
        renamed = client.post(
            f"/api/live/{sid}/speakers", json={"label": "Guest-2", "name": "Thomas"}
        )
        assert renamed.status_code == 200
        assert renamed.json()["speaker_names"] == {"Guest-2": "Thomas"}

        # Raw labels are preserved on the segments (display applies the map).
        rows = client.get(f"/api/live/{sid}/segments").json()
        assert [r["speaker_label"] for r in rows] == ["Guest-2", "Guest-2"]

        # A future phrase with the same label inherits the name at display time.
        client.post(f"/api/live/{sid}/segments", json={"text": "Again", "speaker_label": "Guest-2"})
        assert client.get(f"/api/live/{sid}").json()["speaker_names"] == {"Guest-2": "Thomas"}

        # Clearing (empty name) removes the mapping.
        cleared = client.post(f"/api/live/{sid}/speakers", json={"label": "Guest-2", "name": ""})
        assert cleared.json()["speaker_names"] == {}


def test_display_label_scopes_by_run_prefix() -> None:
    from precursor.backend.services.meeting_analysis import display_label, strip_run_prefix

    # Un-named labels strip their "<run>:" prefix for display.
    assert strip_run_prefix("2:Guest-1") == "Guest-1"
    assert display_label("2:Guest-1", {}) == "Guest-1"
    # A rename is scoped to its own run: renaming run 1's Guest-2 does not
    # touch run 2's Guest-2 (a different voice after the diarization reset).
    names = {"1:Guest-2": "Thomas"}
    assert display_label("1:Guest-2", names) == "Thomas"
    assert display_label("2:Guest-2", names) == "Guest-2"
    # Legacy un-prefixed labels still resolve.
    assert display_label("Guest-3", {}) == "Guest-3"


def test_attendees_roundtrip_and_dedupe() -> None:
    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "Attn"}).json()["id"]
        assert client.get(f"/api/live/{sid}").json()["attendees"] == []
        r = client.put(
            f"/api/live/{sid}/attendees",
            json={"attendees": ["Thomas", " ", "Marie", "Thomas"]},
        )
        assert r.status_code == 200
        assert r.json()["attendees"] == ["Thomas", "Marie"]


class _TextProvider:
    name = "fake"

    async def stream_chat_with_tools(self, **_kwargs):  # type: ignore[no-untyped-def]
        from precursor.backend.services.llm.base import TextDeltaEvent, UsageEvent

        yield TextDeltaEvent(content="## Context\n- It's about the roadmap.")
        yield UsageEvent(prompt_tokens=1, completion_tokens=1, total_tokens=2)


def test_topic_context_summary(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import precursor.backend.services.meeting_summary as summary

    async def _fake_provider(_session, **_kwargs):  # type: ignore[no-untyped-def]
        return _TextProvider()

    monkeypatch.setattr(summary, "get_llm_provider", _fake_provider)

    app = create_app()
    with TestClient(app) as client:
        tid = client.post(
            "/api/topics", json={"title": "Roadmap", "description": "Plan Q3 work"}
        ).json()["id"]
        sid = client.post("/api/live", json={"title": "Ctx", "topic_id": tid}).json()["id"]

        res = client.post(f"/api/live/{sid}/topic-summary")
        assert res.status_code == 200
        assert "Context" in res.json()["summary"]

        # The summary is cached on the session so later opens reuse it instead
        # of re-summarizing.
        cached = client.get(f"/api/live/{sid}").json()["topic_summary"]
        assert "Context" in cached

        # Changing the attached topic invalidates the cache.
        tid2 = client.post("/api/topics", json={"title": "Other"}).json()["id"]
        upd = client.patch(f"/api/live/{sid}", json={"topic_id": tid2})
        assert upd.status_code == 200
        assert upd.json()["topic_summary"] is None

        # No topic → 400.
        sid2 = client.post("/api/live", json={"title": "NoTopic"}).json()["id"]
        assert client.post(f"/api/live/{sid2}/topic-summary").status_code == 400


def test_topic_summary_empty_conversation_returns_empty_not_error() -> None:
    """A topic with nothing to summarize is a normal state, not a 400 error."""
    app = create_app()
    with TestClient(app) as client:
        # Title-only topic: no description, no messages → nothing to summarize.
        tid = client.post("/api/topics", json={"title": "Blank"}).json()["id"]
        sid = client.post("/api/live", json={"title": "Ctx", "topic_id": tid}).json()["id"]
        res = client.post(f"/api/live/{sid}/topic-summary")
        assert res.status_code == 200
        assert res.json()["summary"] == ""
        # Nothing to cache → the session's summary stays null.
        assert client.get(f"/api/live/{sid}").json()["topic_summary"] is None


def test_agenda_endpoint(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import precursor.backend.routers.live as live_router

    async def _fake_agenda(start=None, end=None):  # type: ignore[no-untyped-def]
        return (
            True,
            [
                {
                    "id": "abc",
                    "subject": "Sprint review",
                    "start": "2026-07-12T09:00:00",
                    "end": "2026-07-12T10:00:00",
                    "organizer": "Marie",
                    "attendees": [
                        {"name": "Marie", "email": "m@x"},
                        {"name": "Thomas", "email": None},
                    ],
                    "is_online": True,
                }
            ],
            None,
        )

    monkeypatch.setattr(live_router, "fetch_agenda", _fake_agenda)

    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/live/m365/agenda")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is True
        assert body["events"][0]["subject"] == "Sprint review"


def test_link_meeting_does_not_add_invitees_to_attendees() -> None:
    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "Link"}).json()["id"]
        client.put(f"/api/live/{sid}/attendees", json={"attendees": ["Marie"]})
        r = client.post(
            f"/api/live/{sid}/meeting",
            json={
                "subject": "Sprint review",
                "attendees": [{"name": "Marie"}, {"name": "Thomas"}],
                "is_online": True,
            },
        )
        assert r.status_code == 200
        body = r.json()
        # Invitees are NOT auto-added — only confirmed transcript speakers seed
        # the attendee list; the existing attendee is untouched.
        assert body["attendees"] == ["Marie"]
        assert body["external_meeting"]["subject"] == "Sprint review"


def test_rename_confirmed_speaker_seeds_attendees() -> None:
    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "Confirm"}).json()["id"]
        client.post(f"/api/live/{sid}/segments", json={"text": "Hi", "speaker_label": "1:Guest-2"})
        renamed = client.post(
            f"/api/live/{sid}/speakers", json={"label": "1:Guest-2", "name": "Thomas"}
        )
        assert renamed.status_code == 200
        # Naming a speaker confirms them present → added to the attendee list.
        assert renamed.json()["attendees"] == ["Thomas"]


def test_post_meeting_details_to_topic() -> None:
    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "Roadmap"}).json()["id"]
        sid = client.post("/api/live", json={"title": "M", "topic_id": tid}).json()["id"]
        # No meeting linked yet → 400.
        assert client.post(f"/api/live/{sid}/meeting/post").status_code == 400
        client.post(
            f"/api/live/{sid}/meeting",
            json={
                "subject": "Sprint review",
                "organizer": "Marie",
                "attendees": [{"name": "Marie"}, {"name": "Thomas"}],
                # Graph's bodyPreview is truncated; the full body (HTML) has more.
                "body_preview": "Agenda: ship v2.",
                "body": "<html><body><p>Agenda: ship v2.</p><p>Then discuss the "
                "full roadmap in detail.</p></body></html>",
                "is_online": True,
            },
        )
        r = client.post(f"/api/live/{sid}/meeting/post")
        assert r.status_code == 201
        assert r.json()["topic_id"] == tid
        msgs = client.get(f"/api/topics/{tid}/messages").json()
        # The post uses the full body, not just the truncated preview.
        assert any(
            "Sprint review" in m["content"] and "roadmap in detail" in m["content"] for m in msgs
        )


def test_html_to_text_strips_tags_and_scripts() -> None:
    from precursor.backend.services.meeting_analysis import html_to_text

    out = html_to_text(
        "<html><head><style>p{color:red}</style></head><body>"
        "<p>Hello&nbsp;world</p><script>evil()</script><div>Line two</div></body></html>"
    )
    assert "Hello world" in out
    assert "Line two" in out
    assert "evil" not in out
    assert "color:red" not in out


def test_gc_keeps_meeting_attachment_blobs() -> None:
    import asyncio

    from precursor.backend.services.blob_store import gc_orphan_blobs

    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "Att"}).json()["id"]
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
            b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        att = client.post(
            f"/api/live/{sid}/attachments", files={"file": ("shot.png", png, "image/png")}
        ).json()
        # The startup blob sweep must not delete meeting-note attachments.
        asyncio.run(gc_orphan_blobs())
        served = client.get(att["url"])
        assert served.status_code == 200, served.text


def test_post_summary_copies_attachments_to_topic() -> None:
    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "Roadmap"}).json()["id"]
        sid = client.post("/api/live", json={"title": "M", "topic_id": tid}).json()["id"]
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
            b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        att = client.post(
            f"/api/live/{sid}/attachments", files={"file": ("shot.png", png, "image/png")}
        ).json()
        summary = (
            f"## Notes\nAll good.\n\n## Attachments\n- ![shot](/api/live/attachments/{att['id']})"
        )
        r = client.post(f"/api/live/{sid}/summary/post", json={"summary": summary})
        assert r.status_code == 201
        msgs = client.get(f"/api/topics/{tid}/messages").json()
        posted = next(m for m in msgs if "Meeting summary" in m["content"])
        # The file was copied into the topic message's gallery…
        assert len(posted["attachments"]) == 1
        assert posted["attachments"][0]["original_filename"] == "shot.png"
        # …the copied attachment actually serves its bytes…
        served = client.get(f"/api/attachments/{posted['attachments'][0]['id']}")
        assert served.status_code == 200, served.text
        assert served.headers["content-type"].startswith("image/")
        # …and the raw live-URL reference was stripped from the body.
        assert "/api/live/attachments/" not in posted["content"]


def test_meeting_attachment_upload_and_serve() -> None:
    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "Att"}).json()["id"]
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
            b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        r = client.post(
            f"/api/live/{sid}/attachments",
            files={"file": ("shot.png", png, "image/png")},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["is_image"] is True
        assert body["url"] == f"/api/live/attachments/{body['id']}"
        # The serve URL returns the bytes.
        served = client.get(body["url"])
        assert served.status_code == 200
        assert served.headers["content-type"].startswith("image/png")


def test_notes_persist_via_update() -> None:
    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "N"}).json()["id"]
        assert client.get(f"/api/live/{sid}").json()["notes"] == ""
        r = client.patch(f"/api/live/{sid}", json={"notes": "## Agenda\n- item one"})
        assert r.status_code == 200
        assert r.json()["notes"] == "## Agenda\n- item one"
        # Ending the session can carry a final notes payload.
        r = client.patch(f"/api/live/{sid}", json={"status": "ended", "notes": "final notes"})
        assert r.json()["status"] == "ended"
        assert r.json()["notes"] == "final notes"


def test_context_notes_add_remove_and_read() -> None:
    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "Notes"}).json()["id"]
        assert client.get(f"/api/live/{sid}").json()["context_notes"] == []
        # Add (dedupes).
        client.post(f"/api/live/{sid}/context-notes", json={"text": "Use Redis for cache"})
        r = client.post(f"/api/live/{sid}/context-notes", json={"text": "Use Redis for cache"})
        assert r.json()["context_notes"] == ["Use Redis for cache"]
        client.post(f"/api/live/{sid}/context-notes", json={"text": "Deadline is Friday"})
        # Replace (used for removal).
        r = client.put(f"/api/live/{sid}/context-notes", json={"notes": ["Deadline is Friday"]})
        assert r.json()["context_notes"] == ["Deadline is Friday"]


def test_context_notes_text_helper() -> None:
    from precursor.backend.services.meeting_analysis import context_notes_text

    assert context_notes_text(None) == ""
    assert context_notes_text([]) == ""
    assert context_notes_text(["a", " ", "b"]) == "- a\n- b"


def test_unlink_meeting_clears_meeting_keeps_attendees() -> None:
    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "Unlink"}).json()["id"]
        client.put(f"/api/live/{sid}/attendees", json={"attendees": ["Marie"]})
        client.post(
            f"/api/live/{sid}/meeting",
            json={"subject": "Standup", "attendees": [{"name": "Marie"}], "is_online": True},
        )
        r = client.delete(f"/api/live/{sid}/meeting")
        assert r.status_code == 200
        body = r.json()
        assert body["external_meeting"] is None
        assert body["attendees"] == ["Marie"]


def test_meeting_context_text_includes_body_preview() -> None:
    from precursor.backend.services.meeting_analysis import meeting_context_text

    assert meeting_context_text(None) == ""
    text = meeting_context_text(
        {
            "subject": "Roadmap sync",
            "organizer": "Ludovic",
            "attendees": [{"name": "Marie"}, {"name": "Thomas"}],
            "body_preview": "Discuss Q3 milestones and blockers.",
        }
    )
    assert "Roadmap sync" in text
    assert "Marie" in text
    assert "Q3 milestones" in text


def test_agenda_parser_unwraps_workiq_envelope() -> None:
    from precursor.backend.services.meeting_agenda import _events_from, _normalize_event

    payload = {
        "results": [
            {
                "data": {
                    "value": [
                        {
                            "id": "e1",
                            "subject": "Café du matin",
                            "isOnlineMeeting": True,
                            "start": {"dateTime": "2026-07-12T06:45:00.0000000", "timeZone": "UTC"},
                            "end": {"dateTime": "2026-07-12T07:00:00.0000000", "timeZone": "UTC"},
                            "organizer": {"emailAddress": {"name": "Ludovic", "address": "l@x"}},
                            "attendees": [
                                {"emailAddress": {"name": "Mickaël", "address": "m@x"}},
                            ],
                        }
                    ]
                },
                "statusCode": 200,
            }
        ],
        "note": "Results limited to 50 items per collection.",
    }
    raws = _events_from(payload)
    assert len(raws) == 1
    ev = _normalize_event(raws[0])
    assert ev["subject"] == "Café du matin"
    assert ev["start"] == "2026-07-12T06:45:00.000Z"
    assert ev["organizer"] == "Ludovic"
    assert ev["attendees"] == [{"name": "Mickaël", "email": "m@x"}]

    # Non-200 results are skipped.
    assert _events_from({"results": [{"data": {}, "statusCode": 400, "error": "denied"}]}) == []


def test_ensure_chat_creates_and_reuses() -> None:
    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "Chatty"}).json()["id"]
        assert client.get(f"/api/live/{sid}").json()["chat_id"] is None
        r1 = client.post(f"/api/live/{sid}/chat")
        assert r1.status_code == 200
        cid = r1.json()["id"]
        assert client.get(f"/api/live/{sid}").json()["chat_id"] == cid
        # Idempotent: a second call returns the same chat.
        assert client.post(f"/api/live/{sid}/chat").json()["id"] == cid


def test_live_chat_grounding_includes_context() -> None:
    import asyncio

    from precursor.backend.db import SessionLocal
    from precursor.backend.services.meeting_analysis import live_chat_grounding

    async def grounding_for(chat_id: int) -> str:
        async with SessionLocal() as s:
            return await live_chat_grounding(s, chat_id)

    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "G"}).json()["id"]
        chat = client.post(f"/api/live/{sid}/chat").json()
        client.post(
            f"/api/live/{sid}/segments",
            json={"text": "Discuss the budget", "speaker_label": "1:Guest-1"},
        )
        client.patch(f"/api/live/{sid}", json={"notes": "Remember the deadline"})

        text = asyncio.run(grounding_for(chat["id"]))
        assert "Discuss the budget" in text
        assert "Remember the deadline" in text
        # An unrelated chat id yields no grounding.
        assert asyncio.run(grounding_for(999999)) == ""


def test_session_features_default_dedupe_validate() -> None:
    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "Feat"}).json()["id"]
        assert client.get(f"/api/live/{sid}").json()["features"] == ["insights", "notes"]
        r = client.patch(
            f"/api/live/{sid}",
            json={"features": ["insights", "insights", "bogus", "assistant", "translation"]},
        )
        assert r.status_code == 200
        assert r.json()["features"] == ["insights", "assistant", "translation"]


def test_translate(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import precursor.backend.services.meeting_analysis as ma

    async def _prov(_session, **_kwargs):  # type: ignore[no-untyped-def]
        return _TextProvider()

    async def _model(_session):  # type: ignore[no-untyped-def]
        return "fake"

    async def _effort(_session):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(ma, "get_llm_provider", _prov)
    monkeypatch.setattr(ma, "resolve_live_fast_model", _model)
    monkeypatch.setattr(ma, "resolve_live_reasoning_effort", _effort)

    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "T"}).json()["id"]
        # Translate 400s when there's nothing recorded.
        assert (
            client.post(f"/api/live/{sid}/translate", json={"target_lang": "fr"}).status_code == 400
        )
        client.post(
            f"/api/live/{sid}/segments", json={"text": "Bonjour", "speaker_label": "1:Guest-1"}
        )
        r = client.post(f"/api/live/{sid}/translate", json={"target_lang": "fr"})
        assert r.status_code == 200
        assert r.json()["text"]
        assert r.json()["target_lang"] == "fr"
        # Line mode: returns one translation per input line, aligned.
        lm = client.post(
            f"/api/live/{sid}/translate",
            json={"target_lang": "fr", "texts": ["Hello", "How are you"]},
        )
        assert lm.status_code == 200
        assert len(lm.json()["lines"]) == 2


def test_parse_insights_help_and_suggestion() -> None:
    from precursor.backend.services.meeting_analysis import _parse_insights

    rows, help_needed, sug = _parse_insights(
        '{"insights": [{"kind": "risk", "content": "Tight deadline"}], '
        '"help": true, "suggestion": "Cut scope to the MVP"}'
    )
    assert rows == [("risk", "Tight deadline")]
    assert help_needed is True
    assert sug == "Cut scope to the MVP"
    # help=true but empty suggestion → not actionable.
    assert _parse_insights('{"insights": [], "help": true, "suggestion": ""}') == ([], False, "")
    # Tolerates code fences; no help key → false.
    assert _parse_insights('```json\n{"insights": []}\n```') == ([], False, "")
    # Non-JSON → empty.
    assert _parse_insights("nothing here") == ([], False, "")
