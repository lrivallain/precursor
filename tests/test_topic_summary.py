"""Editable topic summary — API + merge semantics (issue #316).

Covers the three rules the feature hangs on: a topic starts with no summary, a
regeneration on top of a *user-edited* brief is parked for review instead of
overwriting it, and resolving that review keeps exactly the changes the user
accepted.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from precursor.backend.db import SessionLocal
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


def test_hiding_a_topic_without_a_summary_creates_nothing(client: TestClient) -> None:
    topic_id = _topic(client)
    r = client.post(f"/api/topics/{topic_id}/topic-summary/visibility", json={"visible": False})
    assert r.status_code == 200
    assert r.json() is None
    assert client.get(f"/api/topics/{topic_id}/topic-summary").json() is None


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
    proposed = client.post(f"/api/topics/{topic_id}/topic-summary/generate", json={}).json()
    hunks = proposed["suggestion"]["hunks"]
    assert len(hunks) == 2

    r = client.post(
        f"/api/topics/{topic_id}/topic-summary/resolve",
        json={"accepted": [hunks[1]["index"]], "revision": proposed["revision"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "line one\nline two\nline three EDITED"
    assert body["suggestion"] is None
    # Nothing left to review.
    assert (
        client.post(
            f"/api/topics/{topic_id}/topic-summary/resolve",
            json={"accepted": [], "revision": proposed["revision"]},
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


def test_manual_markdown_is_not_sanitized(client: TestClient) -> None:
    path = f"/api/topics/{_topic(client)}/topic-summary"
    content = "```python\nprint('keep my fence')\n```\n"
    assert client.put(path, json={"content": content}).json()["content"] == content


def test_stale_save_cannot_overwrite_a_newer_edit(client: TestClient) -> None:
    path = f"/api/topics/{_topic(client)}/topic-summary"
    original = client.put(path, json={"content": "original"}).json()
    client.put(path, json={"content": "newer"})
    response = client.put(path, json={"content": "stale", "revision": original["revision"]})
    assert response.status_code == 409
    assert client.get(path).json()["content"] == "newer"


def test_review_requires_the_exact_proposal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = f"/api/topics/{_topic(client)}/topic-summary"
    client.put(path, json={"content": "original"})
    proposals = iter(["proposal one", "proposal two"])

    async def generate(session: Any, **kwargs: Any) -> tuple[str, str]:
        return next(proposals), "test"

    monkeypatch.setattr(svc, "generate_summary", generate)
    first = client.post(f"{path}/generate", json={}).json()
    second = client.post(f"{path}/generate", json={}).json()
    response = client.post(f"{path}/resolve", json={"accepted": [0], "revision": first["revision"]})
    assert response.status_code == 409
    assert client.get(path).json() == second


def test_adding_an_item_invalidates_the_old_proposal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = f"/api/topics/{_topic(client)}/topic-summary"
    client.put(path, json={"content": "## Status\noriginal"})

    async def generate(session: Any, **kwargs: Any) -> tuple[str, str]:
        return "## Status\nnew", "test"

    monkeypatch.setattr(svc, "generate_summary", generate)
    client.post(f"{path}/generate", json={})
    result = client.post(f"{path}/items", json={"kind": "todo", "text": "keep me"}).json()
    assert result["suggestion"] is None
    assert "keep me" in result["content"]


def test_generation_does_not_lock_other_writes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Topic

    path = f"/api/topics/{_topic(client)}/topic-summary"

    async def generate(session: Any, **kwargs: Any) -> tuple[str, str]:
        # A separate writer must be able to commit while the provider is busy.
        async with SessionLocal() as writer:
            writer.add(Topic(title="Concurrent write", slug="concurrent-summary-write"))
            await writer.commit()
        return "generated", "test"

    monkeypatch.setattr(svc, "generate_summary", generate)
    response = client.post(f"{path}/generate", json={})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("initial", [None, "generated before"])
def test_generation_cannot_overwrite_an_edit_made_while_waiting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, initial: str | None
) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import TopicSummary
    from precursor.backend.routers.topic_summary import save_summary
    from precursor.backend.schemas import TopicSummarySave

    topic_id = _topic(client)
    path = f"/api/topics/{topic_id}/topic-summary"
    if initial is not None:

        async def seed() -> None:
            async with SessionLocal() as writer:
                writer.add(TopicSummary(topic_id=topic_id, content=initial))
                await writer.commit()

        asyncio.run(seed())

    async def generate(session: Any, **kwargs: Any) -> tuple[str, str]:
        async with SessionLocal() as writer:
            await save_summary(topic_id, TopicSummarySave(content="edited meanwhile"), writer)
        return "stale generation", "test"

    monkeypatch.setattr(svc, "generate_summary", generate)
    response = client.post(f"{path}/generate", json={})
    assert response.status_code == 409, response.text
    assert client.get(path).json()["content"] == "edited meanwhile"


def test_failed_first_generation_creates_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = f"/api/topics/{_topic(client)}/topic-summary"

    async def generate(session: Any, **kwargs: Any) -> tuple[str, str]:
        raise RuntimeError("provider-private-details")

    monkeypatch.setattr(svc, "generate_summary", generate)
    response = client.post(f"{path}/generate", json={})
    assert response.status_code == 502
    assert "provider-private-details" not in response.text
    assert client.get(path).json() is None


def test_generation_does_not_resurrect_a_deleted_summary(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.routers.topic_summary import delete_summary

    topic_id = _topic(client)
    path = f"/api/topics/{topic_id}/topic-summary"
    client.put(path, json={"content": "to be deleted"})

    async def generate(session: Any, **kwargs: Any) -> tuple[str, str]:
        async with SessionLocal() as writer:
            await delete_summary(topic_id, writer)
        return "stale generation", "test"

    monkeypatch.setattr(svc, "generate_summary", generate)
    assert client.post(f"{path}/generate", json={}).status_code == 409
    assert client.get(path).json() is None


def test_an_intentionally_empty_brief_still_requires_review(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = f"/api/topics/{_topic(client)}/topic-summary"
    client.put(path, json={"content": ""})
    monkeypatch.setattr(svc, "generate_summary", AsyncMock(return_value=("proposal", "test")))
    result = client.post(f"{path}/generate", json={}).json()
    assert result["content"] == ""
    assert result["suggestion"]["content"] == "proposal"


def test_visibility_does_not_invalidate_an_editor_revision(client: TestClient) -> None:
    path = f"/api/topics/{_topic(client)}/topic-summary"
    before = client.put(path, json={"content": "original"}).json()
    hidden = client.post(f"{path}/visibility", json={"visible": False}).json()
    assert hidden["revision"] == before["revision"]
    result = client.put(path, json={"content": "edited", "revision": before["revision"]})
    assert result.status_code == 200
    assert result.json()["visible"] is False


def test_stale_delete_keeps_newer_content(client: TestClient) -> None:
    path = f"/api/topics/{_topic(client)}/topic-summary"
    original = client.put(path, json={"content": "original"}).json()
    client.put(path, json={"content": "newer"})
    assert client.delete(path, params={"revision": original["revision"]}).status_code == 409
    assert client.get(path).json()["content"] == "newer"


def test_unchanged_generation_has_no_empty_review(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = f"/api/topics/{_topic(client)}/topic-summary"
    client.put(path, json={"content": "unchanged"})

    async def generate(session: Any, **kwargs: Any) -> tuple[str, str]:
        return "unchanged", "test"

    monkeypatch.setattr(svc, "generate_summary", generate)
    assert client.post(f"{path}/generate", json={}).json()["suggestion"] is None


@pytest.mark.parametrize("preserve", [False, True])
async def test_refresh_prompt_prefers_no_change_for_every_existing_brief(
    monkeypatch: pytest.MonkeyPatch, preserve: bool
) -> None:
    existing = "## My release notes\n- QA approved the build.\n- [ ] Run load test\n"
    complete = AsyncMock(return_value=(existing, None))
    monkeypatch.setattr(svc, "build_context", AsyncMock(return_value="user: QA signed off."))
    monkeypatch.setattr(svc, "get_llm_provider", AsyncMock())
    monkeypatch.setattr(svc, "resolve_llm_model", AsyncMock(return_value="test"))
    monkeypatch.setattr(svc, "complete_text_with_usage", complete)

    text, _ = await svc.generate_summary(
        AsyncMock(),
        topic_id=1,
        title="Release",
        existing=existing,
        preserve=preserve,
        instruction="Keep attention on blockers",
    )

    assert text == existing
    system, user = complete.call_args.kwargs["messages"]
    assert svc._REFRESH_CLAUSE in system.content
    assert svc._INITIAL_CLAUSE not in system.content
    assert (svc._PRESERVE_CLAUSE in system.content) is preserve
    assert "return the existing brief verbatim" in system.content
    assert "Compare meaning, not wording" in system.content
    assert "Do not polish, rephrase" in system.content
    assert "Missing mentions are not evidence" in system.content
    assert "smallest necessary edits" in system.content
    assert existing in user.content
    assert "Keep attention on blockers" in user.content


async def test_an_identical_refresh_preserves_a_user_authored_code_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = "\n```text\nKeep the release gate closed.\n```\n"
    monkeypatch.setattr(svc, "build_context", AsyncMock(return_value="No new facts."))
    monkeypatch.setattr(svc, "get_llm_provider", AsyncMock())
    monkeypatch.setattr(svc, "resolve_llm_model", AsyncMock(return_value="test"))
    monkeypatch.setattr(
        svc, "complete_text_with_usage", AsyncMock(return_value=(existing.strip(), None))
    )
    text, _ = await svc.generate_summary(
        AsyncMock(), topic_id=1, title="Gate", existing=existing, preserve=True
    )
    assert text == existing


@pytest.mark.parametrize("existing", ["", " \n"])
async def test_first_generation_still_uses_the_standard_brief_template(
    monkeypatch: pytest.MonkeyPatch, existing: str
) -> None:
    complete = AsyncMock(return_value=("## Status\n- Starting", None))
    monkeypatch.setattr(svc, "build_context", AsyncMock(return_value="user: Start a pilot."))
    monkeypatch.setattr(svc, "get_llm_provider", AsyncMock())
    monkeypatch.setattr(svc, "resolve_llm_model", AsyncMock(return_value="test"))
    monkeypatch.setattr(svc, "complete_text_with_usage", complete)

    await svc.generate_summary(
        AsyncMock(), topic_id=1, title="Pilot", existing=existing, preserve=True
    )
    system = complete.call_args.kwargs["messages"][0].content
    assert svc._INITIAL_CLAUSE in system
    assert svc._REFRESH_CLAUSE not in system
    assert svc._PRESERVE_CLAUSE not in system


@pytest.mark.parametrize("raw", ["", "   ", "```markdown\n```"])
def test_empty_model_output_keeps_the_existing_brief(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    path = f"/api/topics/{_topic(client)}/topic-summary"
    before = client.put(path, json={"content": "keep this"}).json()
    monkeypatch.setattr(svc, "complete_text_with_usage", AsyncMock(return_value=(raw, None)))
    assert client.post(f"{path}/generate", json={}).status_code == 502
    assert client.get(path).json() == before


def test_generation_uses_bounded_context_and_records_usage(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.models import Attachment, Message, MessageRole, NoteDraft, UsageRecord
    from precursor.backend.services.llm.base import UsageEvent

    topic_id = _topic(client)
    path = f"/api/topics/{topic_id}/topic-summary"

    async def seed() -> None:
        async with SessionLocal() as session:
            for i in range(43):
                session.add(
                    Message(topic_id=topic_id, role=MessageRole.USER, content=f"turn-{i:02}")
                )
            tool = Message(topic_id=topic_id, role=MessageRole.TOOL, content="hidden tool output")
            session.add(tool)
            session.add(NoteDraft(topic_id=topic_id, text="scratchpad fact"))
            await session.flush()
            for name, message_id in [("sent.txt", tool.id), ("unsent.txt", None)]:
                session.add(
                    Attachment(
                        topic_id=topic_id,
                        message_id=message_id,
                        original_filename=name,
                        mime="text/plain",
                        size=1,
                        sha256="0" * 64,
                    )
                )
            await session.commit()

    asyncio.run(seed())
    client.put(path, json={"content": "user-owned"})
    other_topic_id = _topic(client)
    calls: list[Any] = []

    async def complete(provider: Any, **kwargs: Any) -> tuple[str, UsageEvent]:
        calls.append(kwargs["messages"])
        # Exercise the real generation helper, not just the router mock.
        async with SessionLocal() as session:
            session.add(NoteDraft(topic_id=other_topic_id, text="concurrent"))
            await session.commit()
        return "```markdown\nupdated brief\n```", UsageEvent(
            prompt_tokens=10, completion_tokens=5, total_tokens=15
        )

    monkeypatch.setattr(svc, "complete_text_with_usage", complete)
    response = client.post(f"{path}/generate", json={"instruction": "focus on blockers"})
    assert response.status_code == 200, response.text
    assert response.json()["suggestion"]["content"] == "updated brief"
    system, prompt = calls[0]
    assert "authoritative" in system.content
    assert all(
        text in prompt.content
        for text in (
            "turn-03",
            "turn-42",
            "scratchpad fact",
            "sent.txt",
            "user-owned",
            "focus on blockers",
        )
    )
    assert all(
        text not in prompt.content
        for text in (
            "turn-00",
            "turn-02",
            "hidden tool output",
            "unsent.txt",
        )
    )

    async def check_usage() -> None:
        async with SessionLocal() as session:
            usage = (
                await session.scalars(select(UsageRecord).where(UsageRecord.topic_id == topic_id))
            ).one()
            assert (usage.source, usage.total_tokens) == ("/update-summary", 15)

    asyncio.run(check_usage())


def test_atomic_writes_reject_a_stale_snapshot(client: TestClient) -> None:
    topic_id = _topic(client)
    path = f"/api/topics/{topic_id}/topic-summary"
    client.put(path, json={"content": "first"})

    async def race() -> None:
        async with SessionLocal() as first, SessionLocal() as second:
            row = await svc.get_summary(first, topic_id)
            state = svc.snapshot(row)
            await first.commit()
            await svc.write_summary(second, topic_id, state, {"content": "second"})
            with pytest.raises(svc.SummaryConflict):
                await svc.write_summary(first, topic_id, state, {"content": "stale"})

    asyncio.run(race())
    assert client.get(path).json()["content"] == "second"


def test_summary_changes_publish_live_updates(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    topic_id = _topic(client)
    path = f"/api/topics/{topic_id}/topic-summary"
    publish = AsyncMock()
    monkeypatch.setattr(svc, "publish_topic_summary_changed", publish)
    client.put(path, json={"content": "first"})
    client.post(f"{path}/visibility", json={"visible": False})
    client.post(f"{path}/items", json={"kind": "todo", "text": "task"})
    client.delete(path)
    assert publish.await_count == 4
    assert all(call.args == (topic_id,) for call in publish.await_args_list)


def test_invalid_review_indices_do_not_discard_the_proposal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = f"/api/topics/{_topic(client)}/topic-summary"
    client.put(path, json={"content": "original"})
    monkeypatch.setattr(svc, "generate_summary", AsyncMock(return_value=("proposal", "test")))
    proposed = client.post(f"{path}/generate", json={}).json()
    result = client.post(
        f"{path}/resolve", json={"accepted": [-1, 9], "revision": proposed["revision"]}
    )
    assert result.status_code == 422
    assert client.get(path).json() == proposed


def test_blank_items_are_rejected(client: TestClient) -> None:
    path = f"/api/topics/{_topic(client)}/topic-summary"
    assert client.post(f"{path}/items", json={"kind": "todo", "text": "  "}).status_code == 422
    assert client.get(path).json() is None


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


def test_refusing_hunks_preserves_whitespace() -> None:
    base = "\n    indented code\n\n"
    assert svc.apply_hunks(base, svc.diff_hunks(base, "replacement"), set()) == base


def test_missing_action_section_precedes_key_information() -> None:
    result = svc.append_item(
        "## Status\n- ongoing\n\n## Key information\n- fact",
        heading=svc.ACTIONS_HEADING,
        item="- [ ] action",
    )
    assert result.index(svc.STATUS_HEADING) < result.index(svc.ACTIONS_HEADING)
    assert result.index(svc.ACTIONS_HEADING) < result.index(svc.INFO_HEADING)
