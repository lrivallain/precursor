"""Live meeting assistant API tests — session CRUD lifecycle.

Phase 1 covers the session lifecycle only (create / list / get / update /
delete) plus the optional topic link. Transcript ingestion, live analysis, and
summary attachment are exercised in later phases.
"""

from __future__ import annotations

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
    assert _parse_insights(good) == [("action_item", "Ship the fix")]

    fenced = "```json\n" + good + "\n```"
    assert _parse_insights(fenced) == [("action_item", "Ship the fix")]

    prose = "Here you go:\n" + good + "\nHope that helps!"
    assert _parse_insights(prose) == [("action_item", "Ship the fix")]

    # Unknown kinds are dropped; empty content is dropped.
    mixed = '{"insights": [{"kind":"bogus","content":"x"},{"kind":"risk","content":""}]}'
    assert _parse_insights(mixed) == []

    assert _parse_insights("not json at all") == []


def test_meeting_analyze_persists_snapshot(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import precursor.backend.services.meeting_analysis as analysis
    from precursor.backend.services.llm.base import TextDeltaEvent, UsageEvent

    class _FakeProvider:
        name = "fake"

        async def stream_chat_with_tools(self, **_kwargs):  # type: ignore[no-untyped-def]
            payload = (
                '{"insights": ['
                '{"kind": "decision", "content": "Use Postgres"},'
                '{"kind": "action_item", "content": "Draft the schema"}]}'
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
        assert client.post(f"/api/live/{sid}/analyze").json() == []

        client.post(f"/api/live/{sid}/segments", json={"text": "Let's use Postgres"})
        result = client.post(f"/api/live/{sid}/analyze")
        assert result.status_code == 200
        kinds = {i["kind"] for i in result.json()}
        assert kinds == {"decision", "action_item"}

        # The snapshot is readable and replaced (not appended) on re-analysis.
        assert len(client.get(f"/api/live/{sid}/insights").json()) == 2
        assert len(client.post(f"/api/live/{sid}/analyze").json()) == 2


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

        # Post the summary into the linked topic → a new assistant message.
        posted = client.post(
            f"/api/live/{sid}/summary/post", json={"summary": "## Summary\nAll good."}
        )
        assert posted.status_code == 201
        assert posted.json()["topic_id"] == tid
        msgs = client.get(f"/api/topics/{tid}/messages").json()
        assert any("Meeting summary" in m["content"] for m in msgs)


def test_meeting_summary_post_requires_topic() -> None:
    app = create_app()
    with TestClient(app) as client:
        sid = client.post("/api/live", json={"title": "No topic"}).json()["id"]
        client.post(f"/api/live/{sid}/segments", json={"text": "hi"})
        resp = client.post(f"/api/live/{sid}/summary/post", json={"summary": "x"})
        assert resp.status_code == 400


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
