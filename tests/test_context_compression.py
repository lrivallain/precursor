"""Context compression: hygiene passes, compaction markers, and ``/compact``.

A long conversation used to reach the model with every screenshot's base64 as
text and every old tool result in full, and past the budget ``trim_messages``
dropped the oldest turns without a trace. These pin the three remedies: free
hygiene on what the model sees, a non-destructive compaction marker the history
starts from, and the ``/compact`` endpoints for topics, chats and agents.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from mcp.types import CallToolResult, ImageContent, TextContent

from precursor.backend.main import create_app
from precursor.backend.models import MESSAGE_KIND_COMPACTION, Message, MessageRole
from precursor.backend.services import compaction as compaction_mod
from precursor.backend.services import events as events_mod
from precursor.backend.services.context_budget import (
    STALE_TOOL_RESULT_MIN_CHARS,
    elide_stale_tool_results,
    strip_inline_binaries,
    trim_messages,
)
from precursor.backend.services.llm.base import ChatMessage, UsageEvent
from precursor.backend.services.llm.one_shot import OneShotResult
from precursor.backend.services.turn_engine import format_tool_result, hydrate_history

_B64 = "iVBORw0KGgo" + "A" * 4000 + "=="


# -- Hygiene ---------------------------------------------------------------


def test_strip_inline_binaries_replaces_an_image_blocks_payload() -> None:
    text = (
        'Took a screenshot.\n\n{"type": "image", "data": "' + _B64 + '", "mimeType": "image/png"}'
    )
    out = strip_inline_binaries(text)
    assert _B64 not in out
    assert out.startswith("Took a screenshot.")
    block = json.loads(out.split("\n\n", 1)[1])
    assert block["type"] == "image"
    assert block["mimeType"] == "image/png"
    assert block["data"].startswith("[binary omitted: ~")


def test_strip_inline_binaries_handles_blobs_and_data_uris() -> None:
    blob = '{"blob": "' + _B64 + '"}'
    assert "[binary omitted" in strip_inline_binaries(blob)
    uri = "see data:image/jpeg;base64," + _B64 + " here"
    out = strip_inline_binaries(uri)
    assert out.startswith("see data:image/jpeg;base64,[omitted: ~")
    assert out.endswith(" here")


def test_strip_inline_binaries_leaves_ordinary_text_alone() -> None:
    short = '{"data": "aGVsbG8="}'
    assert strip_inline_binaries(short) == short
    prose = '{"data": "' + "word " * 500 + '"}'
    assert strip_inline_binaries(prose) == prose


def test_format_tool_result_drops_image_bytes_but_keeps_the_block_shape() -> None:
    payload = CallToolResult(
        content=[
            TextContent(type="text", text="Saved slide.png"),
            ImageContent(type="image", data=_B64, mime_type="image/png"),
        ]
    )
    out = format_tool_result(payload)
    assert _B64 not in out
    assert out.startswith("Saved slide.png")
    assert json.loads(out.split("\n\n", 1)[1])["mimeType"] == "image/png"


def test_trim_messages_strips_binaries_from_tool_results() -> None:
    tool = ChatMessage(role="tool", content='{"data": "' + _B64 + '"}', tool_call_id="c1")
    out = trim_messages(
        [ChatMessage(role="user", content="hi"), tool],
        max_input_tokens=100_000,
        per_message_max_tokens=100_000,
    )
    assert _B64 not in out[1].content
    assert _B64 in tool.content  # the caller's message is not mutated


def _turn(n: int, result: str) -> list[ChatMessage]:
    return [
        ChatMessage(role="user", content=f"question {n}"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": f"c{n}", "type": "function", "function": {"name": "read"}}],
        ),
        ChatMessage(role="tool", content=result, tool_call_id=f"c{n}", name="read"),
        ChatMessage(role="assistant", content=f"answer {n}"),
    ]


def test_elide_stale_tool_results_keeps_the_last_turns_in_full() -> None:
    big = "x" * (STALE_TOOL_RESULT_MIN_CHARS + 500)
    small = "y" * 100
    history = [*_turn(1, big), *_turn(2, small), *_turn(3, big), *_turn(4, big)]
    out = elide_stale_tool_results(history, keep_turns=2)
    tools = [m for m in out if m.role == "tool"]
    assert "elided" in tools[0].content and len(tools[0].content) < len(big)
    assert "`read`" in tools[0].content
    assert tools[1].content == small  # under the size threshold
    assert tools[2].content == big  # within the last two user turns
    assert tools[3].content == big
    assert history[2].content == big  # input untouched
    # Pairing is untouched: same roles, same tool_call ids.
    assert [(m.role, m.tool_call_id) for m in out] == [(m.role, m.tool_call_id) for m in history]


def test_elide_stale_tool_results_is_off_at_zero_and_for_short_histories() -> None:
    big = "x" * (STALE_TOOL_RESULT_MIN_CHARS + 1)
    history = [*_turn(1, big), *_turn(2, big)]
    assert elide_stale_tool_results(history, keep_turns=0) == history
    assert elide_stale_tool_results(history, keep_turns=5) == history


# -- Hydration from a compaction marker --------------------------------------


def _row(role: MessageRole, content: str, *, kind: str | None = None) -> Message:
    return Message(role=role, content=content, kind=kind, topic_id=1, attachments=[])


def test_history_starts_at_the_latest_marker_with_its_summary() -> None:
    rows = [
        _row(MessageRole.USER, "old question"),
        _row(MessageRole.ASSISTANT, "old answer"),
        _row(MessageRole.SYSTEM, "first summary", kind=MESSAGE_KIND_COMPACTION),
        _row(MessageRole.USER, "middle"),
        _row(MessageRole.SYSTEM, "second summary", kind=MESSAGE_KIND_COMPACTION),
        _row(MessageRole.USER, "new question"),
    ]
    history = hydrate_history(rows)
    assert [m.role for m in history] == ["user"]
    assert "second summary" in history[0].content
    assert "first summary" not in history[0].content
    assert history[0].content.endswith("new question")
    assert "old question" not in history[0].content


def test_a_skill_prompt_override_keeps_the_summary() -> None:
    rows = [
        _row(MessageRole.SYSTEM, "the summary", kind=MESSAGE_KIND_COMPACTION),
        _row(MessageRole.USER, "/to-en bravo"),
    ]
    history = hydrate_history(rows, prompt_override="Translate: bravo")
    assert "the summary" in history[0].content
    assert history[0].content.endswith("Translate: bravo")


def test_summary_gets_its_own_user_turn_when_history_opens_with_the_assistant() -> None:
    rows = [
        _row(MessageRole.SYSTEM, "the summary", kind=MESSAGE_KIND_COMPACTION),
        _row(MessageRole.ASSISTANT, "a scheduled note"),
    ]
    history = hydrate_history(rows)
    assert [m.role for m in history] == ["user", "assistant"]
    assert "the summary" in history[0].content


# -- /compact for topics and chats --------------------------------------------


def _fake_llm(monkeypatch: pytest.MonkeyPatch, reply: str = "## Goal\nShip it") -> list[str]:
    seen: list[str] = []

    async def _complete_once(_session, *, system, user, usage_source, **kwargs):
        _ = system, kwargs
        assert usage_source == "/compact"
        seen.append(user)
        return OneShotResult(
            text=reply,
            model="fake-model",
            usage=UsageEvent(prompt_tokens=120, completion_tokens=30, total_tokens=150),
        )

    monkeypatch.setattr(compaction_mod, "complete_once", _complete_once)
    return seen


def _seed(kind: str, container_id: int) -> None:
    import asyncio

    from precursor.backend.db import SessionLocal

    async def _go() -> None:
        fk = {"topic_id": container_id} if kind == "topic" else {"chat_id": container_id}
        async with SessionLocal() as s:
            for role, content in (
                (MessageRole.USER, "What is AVS?"),
                (MessageRole.ASSISTANT, "Azure VMware Solution."),
            ):
                s.add(Message(role=role, content=content, **fk))
                await s.flush()
            await s.commit()

    asyncio.run(_go())


@pytest.mark.parametrize("kind", ["topic", "chat"])
def test_compact_appends_a_marker_and_undo_restores_history(monkeypatch, kind) -> None:
    seen = _fake_llm(monkeypatch)
    app = create_app()
    with TestClient(app) as client:
        if kind == "topic":
            cid = client.post("/api/topics", json={"title": "Compact me"}).json()["id"]
            base = f"/api/topics/{cid}/messages"
        else:
            cid = client.post("/api/chats", json={"title": "Compact me"}).json()["id"]
            base = f"/api/chats/{cid}/messages"

        empty = client.post(f"{base}/compact", json={})
        assert empty.status_code == 409

        _seed(kind, cid)
        before = client.get(f"{base}/context").json()
        assert before["compacted"] is False and before["tokens"] > 0

        res = client.post(f"{base}/compact", json={"instructions": "keep the SKUs"})
        assert res.status_code == 201, res.text
        marker = res.json()
        assert marker["kind"] == "compaction"
        assert marker["role"] == "system"
        assert marker["content"] == "## Goal\nShip it"
        assert marker["prompt_tokens"] == 120
        assert "What is AVS?" in seen[0] and "keep the SKUs" in seen[0]

        rows = client.get(base).json()
        assert [r["kind"] for r in rows][-1] == "compaction"
        assert len(rows) == 3  # older rows are kept for the reader

        after = client.get(f"{base}/context").json()
        assert after["compacted"] is True
        assert after["compaction_id"] == marker["id"]
        assert after["compacted_messages"] == 2

        # Compacting again folds the previous summary in.
        client.post(f"{base}/compact", json={})
        assert "Ship it" in seen[-1]

        for row in client.get(base).json():
            if row["kind"] == "compaction":
                assert client.delete(f"{base}/{row['id']}").status_code == 204
        assert client.get(f"{base}/context").json()["compacted"] is False


def test_compact_is_refused_while_a_reply_is_generating(monkeypatch) -> None:
    _fake_llm(monkeypatch)
    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "Busy"}).json()["id"]
        _seed("topic", tid)
        events_mod._stream_delta("topic", tid, 1)
        try:
            res = client.post(f"/api/topics/{tid}/messages/compact", json={})
            assert res.status_code == 409
            assert "still being generated" in res.json()["detail"]
        finally:
            events_mod._stream_delta("topic", tid, -1)
        assert not events_mod.is_streaming("topic", tid)


def test_chunks_split_an_oversized_transcript() -> None:
    blocks = ["a" * 40, "b" * 40, "c" * 40]
    assert compaction_mod.chunk_blocks(blocks, 100) == ["a" * 40 + "\n\n" + "b" * 40, "c" * 40]


# -- Agents -----------------------------------------------------------------


async def test_agent_compact_command_calls_the_sdk(monkeypatch) -> None:
    from copilot.generated.rpc import Trigger

    from precursor.backend.services.agents.manager import AgentManager

    calls: list[object] = []

    class _History:
        async def compact(self, params, *, timeout=None):
            calls.append((params, timeout))
            return SimpleNamespace(success=True)

    live = SimpleNamespace(sdk_session=SimpleNamespace(rpc=SimpleNamespace(history=_History())))
    run = SimpleNamespace(id=7, status="idle", copilot_session_id="sid")
    mgr = AgentManager()

    async def _load(_agent_id):
        return SimpleNamespace(id=1)

    async def _resolve_run(_agent_id):
        return run

    async def _ensure_live(_agent, _run):
        return live

    async def _publish(*_args, **_kwargs):
        return None

    monkeypatch.setattr(mgr, "_load", _load)
    monkeypatch.setattr(mgr, "_resolve_run", _resolve_run)
    monkeypatch.setattr(mgr, "_ensure_live", _ensure_live)
    monkeypatch.setattr(mgr, "_publish", _publish)

    await mgr.run_command(1, "compact", " the budget ")
    params, _timeout = calls[0]
    assert params.custom_instructions == "the budget"
    assert params.trigger is Trigger.MANUAL

    run.status = "running"
    with pytest.raises(ValueError, match="busy"):
        await mgr.run_command(1, "compact", "")


def test_compaction_events_are_normalised_for_the_timeline() -> None:
    from copilot.generated.session_events import CompactionTrigger, SessionCompactionCompleteData

    from precursor.backend.services.agents.event_normalizer import normalize_event

    data = SessionCompactionCompleteData(
        success=True,
        pre_compaction_tokens=180_000,
        post_compaction_tokens=24_000,
        messages_removed=42,
        token_limit=200_000,
        summary_content="## Goal\nKeep going",
        trigger=CompactionTrigger.THRESHOLD,
    )
    ev = normalize_event(SimpleNamespace(data=data))
    assert ev.kind == "compaction"
    assert ev.text == "## Goal\nKeep going"
    assert ev.data == {
        "pre_compaction_tokens": 180_000,
        "post_compaction_tokens": 24_000,
        "messages_removed": 42,
        "token_limit": 200_000,
        "trigger": "threshold",
        "success": True,
    }
