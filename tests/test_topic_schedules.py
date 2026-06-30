"""Topic schedule tests — recurrence attached to an ordinary topic.

After the refactor a topic is "scheduled" simply when it has a TopicSchedule
row; there is no special topic kind. These cover the nested HTTP surface under
``/api/topics/{id}/schedule`` and that the schedule shows up on topic reads.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.main import create_app


def _make_topic(client: TestClient, title: str = "Inbox") -> int:
    created = client.post("/api/topics", json={"title": title})
    assert created.status_code in (200, 201)
    return created.json()["id"]


def test_topic_schedule_crud() -> None:
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)

        # A fresh topic has no schedule and is a standard topic.
        assert client.get(f"/api/topics/{topic_id}/schedule").status_code == 404
        body = client.get(f"/api/topics/{topic_id}").json()
        assert body["kind"] == "standard"
        assert body["schedule"] is None

        created = client.post(
            f"/api/topics/{topic_id}/schedule",
            json={"prompt": "Summarise my inbox", "interval_seconds": 300},
        )
        assert created.status_code == 201
        sched = created.json()
        assert sched["enabled"] is True
        assert sched["prompt"] == "Summarise my inbox"
        assert sched["next_run_at"] is not None

        # Embedded in the topic read + the tree.
        assert client.get(f"/api/topics/{topic_id}").json()["schedule"]["interval_seconds"] == 300
        tree = client.get("/api/topics/tree").json()
        node = next(n for n in tree if n["id"] == topic_id)
        assert node["schedule"] is not None

        # Creating twice conflicts.
        assert (
            client.post(
                f"/api/topics/{topic_id}/schedule",
                json={"prompt": "again", "interval_seconds": 600},
            ).status_code
            == 409
        )

        # Editing the prompt + pausing.
        updated = client.patch(
            f"/api/topics/{topic_id}/schedule",
            json={"prompt": "Summarise only urgent mail", "enabled": False},
        ).json()
        assert updated["prompt"] == "Summarise only urgent mail"
        assert updated["enabled"] is False
        assert updated["next_run_at"] is None

        # Deleting the schedule keeps the topic.
        assert client.delete(f"/api/topics/{topic_id}/schedule").status_code == 204
        assert client.get(f"/api/topics/{topic_id}/schedule").status_code == 404
        assert client.get(f"/api/topics/{topic_id}").status_code == 200


def test_topic_schedule_run_now_posts_notice() -> None:
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)
        client.post(
            f"/api/topics/{topic_id}/schedule",
            json={"prompt": "do it", "interval_seconds": 86400},
        )
        ran = client.post(f"/api/topics/{topic_id}/schedule/run")
        assert ran.status_code == 200
        assert ran.json()["next_run_at"] is not None
        # A "Run now accepted" system message lands in the topic.
        msgs = client.get(f"/api/topics/{topic_id}/messages").json()
        assert any("Run now accepted" in m["content"] for m in msgs)
