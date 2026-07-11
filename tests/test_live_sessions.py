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
