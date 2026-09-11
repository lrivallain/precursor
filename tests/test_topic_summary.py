"""Editable topic summary — API + merge semantics (issue #316).

Covers the three rules the feature hangs on: a topic starts with no summary, a
regeneration on top of a *user-edited* brief is parked for review instead of
overwriting it, and resolving that review keeps exactly the changes the user
accepted.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services import topic_summary as svc


@pytest.fixture
def client() -> Any:
    app = create_app()
    with TestClient(app) as c:
        yield c


def _topic(client: TestClient, title: str = "Summary topic") -> int:
    created = client.post("/api/topics", json={"title": title})
    assert created.status_code in (200, 201)
    return int(created.json()["id"])


def test_topic_starts_without_a_summary(client: TestClient) -> None:
    topic_id = _topic(client)
    r = client.get(f"/api/topics/{topic_id}/topic-summary")
    assert r.status_code == 200
    assert r.json() is None


def test_manual_edit_is_saved_and_marks_the_summary_user_owned(client: TestClient) -> None:
    topic_id = _topic(client)
    r = client.put(
        f"/api/topics/{topic_id}/topic-summary",
        json={"content": "## Status\n- Drafting"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "## Status\n- Drafting"
    assert body["user_edited"] is True
    assert body["visible"] is True
    assert client.get(f"/api/topics/{topic_id}/topic-summary").json()["content"].startswith("##")


def test_visibility_toggles_without_losing_the_text(client: TestClient) -> None:
    topic_id = _topic(client)
    client.put(f"/api/topics/{topic_id}/topic-summary", json={"content": "## Status\n- Here"})
    r = client.post(f"/api/topics/{topic_id}/topic-summary/visibility", json={"visible": False})
    assert r.status_code == 200
    assert r.json()["visible"] is False
    assert r.json()["content"] == "## Status\n- Here"


def test_items_append_under_the_right_section(client: TestClient) -> None:
    topic_id = _topic(client)
    client.post(
        f"/api/topics/{topic_id}/topic-summary/items",
        json={"kind": "todo", "text": "Ping the reviewer"},
    )
    r = client.post(
        f"/api/topics/{topic_id}/topic-summary/items",
        json={"kind": "important", "text": "Deploy window closes Friday"},
    )
    content = r.json()["content"]
    assert "## Actions\n- [ ] Ping the reviewer" in content
    assert "## Key information\n- Deploy window closes Friday" in content
    # Repeating a command is a no-op rather than a duplicate line.
    again = client.post(
        f"/api/topics/{topic_id}/topic-summary/items",
        json={"kind": "todo", "text": "Ping the reviewer"},
    )
    assert again.json()["content"].count("Ping the reviewer") == 1


def test_generate_replaces_an_untouched_summary_but_reviews_an_edited_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    topic_id = _topic(client)

    async def fake_generate(session: Any, **kwargs: Any) -> tuple[str, str]:
        return ("## Status\n- Generated\n\n## Actions\n- [ ] Do the thing", "test-model")

    monkeypatch.setattr(svc, "generate_summary", fake_generate)

    # No summary yet → the generated text lands straight in.
    r = client.post(f"/api/topics/{topic_id}/topic-summary/generate", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["content"].startswith("## Status\n- Generated")
    assert body["model"] == "test-model"
    assert body["suggestion"] is None

    # Once the user edits, a refresh must not overwrite: it proposes.
    client.put(
        f"/api/topics/{topic_id}/topic-summary",
        json={"content": "## Status\n- Mine\n\n## Actions\n- [ ] Keep this"},
    )
    r = client.post(f"/api/topics/{topic_id}/topic-summary/generate", json={})
    body = r.json()
    assert body["content"] == "## Status\n- Mine\n\n## Actions\n- [ ] Keep this"
    assert body["suggestion"] is not None
    assert body["suggestion"]["hunks"], "a proposal must be reviewable change by change"


def test_resolve_keeps_only_accepted_changes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    topic_id = _topic(client)
    client.put(
        f"/api/topics/{topic_id}/topic-summary",
        json={"content": "line one\nline two\nline three"},
    )

    async def fake_generate(session: Any, **kwargs: Any) -> tuple[str, str]:
        return ("line one EDITED\nline two\nline three EDITED", "test-model")

    monkeypatch.setattr(svc, "generate_summary", fake_generate)
    hunks = client.post(f"/api/topics/{topic_id}/topic-summary/generate", json={}).json()[
        "suggestion"
    ]["hunks"]
    assert len(hunks) == 2

    r = client.post(
        f"/api/topics/{topic_id}/topic-summary/resolve",
        json={"accepted": [hunks[1]["index"]]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "line one\nline two\nline three EDITED"
    assert body["suggestion"] is None
    # Nothing left to review.
    assert (
        client.post(
            f"/api/topics/{topic_id}/topic-summary/resolve", json={"accepted": []}
        ).status_code
        == 404
    )


def test_delete_returns_the_topic_to_having_no_summary(client: TestClient) -> None:
    topic_id = _topic(client)
    client.put(f"/api/topics/{topic_id}/topic-summary", json={"content": "## Status\n- x"})
    assert client.delete(f"/api/topics/{topic_id}/topic-summary").status_code == 204
    assert client.get(f"/api/topics/{topic_id}/topic-summary").json() is None


def test_unknown_topic_is_404(client: TestClient) -> None:
    assert client.get("/api/topics/999999/topic-summary").status_code == 404


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_diff_and_apply_round_trip() -> None:
    base = "a\nb\nc"
    proposed = "a\nB\nc\nd"
    hunks = svc.diff_hunks(base, proposed)
    assert [h.index for h in hunks] == [0, 1]
    assert svc.apply_hunks(base, hunks, set()).strip() == base
    assert svc.apply_hunks(base, hunks, {0, 1}).strip() == proposed
    assert svc.apply_hunks(base, hunks, {1}).strip() == "a\nb\nc\nd"


def test_append_item_creates_missing_sections_in_order() -> None:
    content = svc.append_item("", heading=svc.ACTIONS_HEADING, item="- [ ] first")
    content = svc.append_item(content, heading=svc.ACTIONS_HEADING, item="- [ ] second")
    assert content.strip() == "## Actions\n- [ ] first\n- [ ] second"


def test_sanitize_summary_strips_a_wrapping_fence() -> None:
    assert svc.sanitize_summary("```markdown\n## Status\n- ok\n```") == "## Status\n- ok"


def test_format_helpers_normalise_free_text() -> None:
    assert svc.format_todo("call Bob") == "- [ ] call Bob"
    assert svc.format_todo("- [x] done") == "- [x] done"
    assert svc.format_important("api v2 only") == "- api v2 only"
