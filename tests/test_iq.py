"""Precursor IQ: indexing, hybrid retrieval, gating and cited answers."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from precursor.backend.db import SessionLocal, init_db
from precursor.backend.main import create_app
from precursor.backend.models import (
    AppSetting,
    Chat,
    IQChunk,
    IQDirty,
    MeetingSegment,
    MeetingSession,
    Memory,
    Message,
    MessageRole,
    Topic,
)
from precursor.backend.services.iq import indexer
from precursor.backend.services.iq.ask import ask, cited_numbers
from precursor.backend.services.iq.retrieve import query_tokens, retrieve
from precursor.backend.services.iq.sources import CHUNK_CHARS, chunk_text
from precursor.backend.services.mcp import precursor_server as ps


@pytest.fixture(autouse=True)
async def _migrated() -> None:
    await init_db()


async def _set(key: str, value: object) -> None:
    async with SessionLocal() as session:
        row = await session.get(AppSetting, key)
        if row is None:
            session.add(AppSetting(key=key, value=json.dumps(value)))
        else:
            row.value = json.dumps(value)
        await session.commit()


async def _topic(title: str, *, description: str = "", messages: tuple[str, ...] = ()) -> int:
    async with SessionLocal() as session:
        topic = Topic(
            title=title, slug=f"iq-{title.lower().replace(' ', '-')}", description=description
        )
        session.add(topic)
        await session.flush()
        for i, content in enumerate(messages):
            role = MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT
            session.add(Message(topic_id=topic.id, role=role, content=content))
        await session.commit()
        return topic.id


async def _indexed() -> None:
    for _ in range(50):
        if not await indexer.drain(max_sources=500):
            return
    raise AssertionError("the IQ queue never drained")


# -- chunking ---------------------------------------------------------------


def test_chunk_text_windows_long_text_with_overlap() -> None:
    para = "word " * 200
    text = "\n\n".join([para.strip()] * 5)
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert all(len(c) <= CHUNK_CHARS for c in chunks)
    # Nothing is lost: every chunk is a slice of the source.
    assert all(c in text for c in chunks)


def test_chunk_text_short_and_empty() -> None:
    assert chunk_text("") == []
    assert chunk_text("  hello  ") == ["hello"]


def test_query_tokens_fold_accents_and_drop_stopwords() -> None:
    assert query_tokens("Où est la Réunion de Kubernetes?") == ["reunion", "kubernetes"]
    # All stopwords: keep them rather than search for nothing.
    assert query_tokens("the and") == ["the", "and"]


def test_cited_numbers_dedupes_in_order() -> None:
    assert cited_numbers("A [^2] b [^1] c [^2]") == [2, 1]


# -- indexing + retrieval ---------------------------------------------------


async def test_flush_hook_queues_changes_and_drain_indexes_them() -> None:
    tid = await _topic("Qzfrobnicate ledger", messages=("hello qzfrobnicate world",))
    async with SessionLocal() as session:
        queued = (
            await session.execute(
                select(IQDirty.source_kind).where(
                    IQDirty.source_kind == "topic", IQDirty.source_id == tid
                )
            )
        ).all()
    assert queued
    await _indexed()
    async with SessionLocal() as session:
        chunks = (
            (
                await session.execute(
                    select(IQChunk).where(
                        IQChunk.container_kind == "topic", IQChunk.container_id == tid
                    )
                )
            )
            .scalars()
            .all()
        )
    kinds = {(c.source_kind, c.section) for c in chunks}
    assert ("topic", "topics") in kinds
    assert ("message", "messages") in kinds


async def test_retrieve_matches_words_out_of_order_and_ranks_title_first() -> None:
    title_id = await _topic("Zwazzle latency budget", description="Tracking p99 targets")
    body_id = await _topic(
        "Unrelated heading",
        messages=("The zwazzle service blew its latency budget after the deploy.",),
    )
    result = await retrieve("latency zwazzle")
    ids = [(h.section, h.entity_id) for h in result.hits]
    assert ("topics", title_id) in ids
    assert ("topics", body_id) in ids
    assert result.hits[0].entity_id == title_id
    assert result.hits[0].is_title
    body_hit = next(h for h in result.hits if h.entity_id == body_id)
    assert body_hit.field == "message"
    assert "zwazzle" in body_hit.snippet.lower()
    assert result.lexical_backend in {"fts5", "like"}
    # Grounding block numbers every hit for citation.
    assert "[^1]" in result.markdown
    assert f"[^{len(result.hits)}]" in result.markdown


async def test_retrieve_is_accent_insensitive() -> None:
    tid = await _topic("Qwyrzt réunion trimestrielle")
    result = await retrieve("qwyrzt reunion")
    assert any(h.entity_id == tid for h in result.hits)


async def test_archived_containers_are_hidden_without_reindexing() -> None:
    tid = await _topic("Plumbus archival check")
    assert any(h.entity_id == tid for h in (await retrieve("plumbus archival")).hits)
    async with SessionLocal() as session:
        topic = await session.get(Topic, tid)
        assert topic is not None
        from datetime import UTC, datetime

        topic.archived_at = datetime.now(UTC)
        await session.commit()
    assert not any(h.entity_id == tid for h in (await retrieve("plumbus archival")).hits)


async def test_deleted_source_drops_its_chunks() -> None:
    tid = await _topic("Grommet teardown", messages=("grommet fasteners everywhere",))
    await _indexed()
    async with SessionLocal() as session:
        msg = (await session.execute(select(Message).where(Message.topic_id == tid))).scalar_one()
        await session.delete(msg)
        await session.commit()
    await _indexed()
    result = await retrieve("grommet fasteners")
    assert not any(h.source_kind == "message" and h.entity_id == tid for h in result.hits)


async def test_reconcile_catches_bulk_writes() -> None:
    tid = await _topic("Snorkel bulk", messages=("original snorkel text",))
    await _indexed()
    async with SessionLocal() as session:
        # Core statements bypass the ORM flush hook.
        await session.execute(
            update(Message)
            .where(Message.topic_id == tid)
            .values(content="rewritten flibbertigibbet text")
        )
        await session.execute(delete(IQDirty))
        await session.commit()
    await _indexed()
    assert not (await retrieve("flibbertigibbet")).hits
    await indexer.reconcile()
    await _indexed()
    assert any(h.entity_id == tid for h in (await retrieve("flibbertigibbet")).hits)

    async with SessionLocal() as session:
        # By id, not container: SQLite reuses freed ids, so an earlier test's
        # deleted topic can leave chunks under this topic's id that reconcile
        # rightly keeps, because their message ids now belong to live rows.
        gone = (
            (await session.execute(select(Message.id).where(Message.topic_id == tid)))
            .scalars()
            .all()
        )
        await session.execute(delete(Message).where(Message.topic_id == tid))
        await session.commit()
    await indexer.reconcile()
    async with SessionLocal() as session:
        left = (
            await session.execute(
                select(IQChunk.id).where(
                    IQChunk.source_kind == "message", IQChunk.source_id.in_(gone)
                )
            )
        ).all()
    assert gone
    assert left == []


async def test_live_transcript_and_memory_are_indexed() -> None:
    async with SessionLocal() as session:
        live = MeetingSession(title="Standup", slug="iq-standup-transcript")
        session.add(live)
        await session.flush()
        session.add(MeetingSegment(session_id=live.id, text="we discussed the wibblewock rollout"))
        session.add(Memory(kind="fact", content="Prefers wibblewock dashboards in dark mode"))
        await session.commit()
        live_id = live.id
    result = await retrieve("wibblewock")
    fields = {(h.section, h.field) for h in result.hits}
    assert ("live", "transcript") in fields
    assert ("memory", "memory") in fields
    assert any(h.entity_id == live_id for h in result.hits)
    # The palette leaves memory out.
    palette = await retrieve("wibblewock", containers={"topic", "chat", "agent", "live"})
    assert all(h.section != "memory" for h in palette.hits)


async def test_gates_filter_by_exposure_section() -> None:
    tid = await _topic("Gatekeeper meta", messages=("gatekeeper secret conversation",))
    async with SessionLocal() as session:
        session.add(Chat(title="Gatekeeper chat", slug="iq-gatekeeper-chat"))
        await session.commit()
    only_topics = await retrieve("gatekeeper", gates={"topics"})
    assert only_topics.hits
    assert all(h.gate == "topics" for h in only_topics.hits)
    assert any(h.entity_id == tid and h.field == "title" for h in only_topics.hits)
    assert (await retrieve("gatekeeper", gates=set())).hits == []


# -- embeddings -------------------------------------------------------------


async def test_embeddings_add_semantic_candidates() -> None:
    tid = await _topic("Xylophonic", messages=("The xylophonics clustering jobs finished",))
    await _set("iq_embeddings_enabled", True)
    try:
        await _indexed()
        for _ in range(50):
            if not await indexer.embed_pending(max_batches=20):
                break
        async with SessionLocal() as session:
            vec = await session.scalar(
                select(IQChunk.embedding_model).where(
                    IQChunk.container_kind == "topic", IQChunk.container_id == tid
                )
            )
        assert vec == "mock:text-embedding-3-small"
        # "clusterization" shares no FTS prefix with "clustering" beyond the
        # mock's stem, so only the vector side can find it.
        result = await retrieve("xylophonicss clusterization")
        assert result.semantic is True
        assert any(h.entity_id == tid for h in result.hits)
        status = await indexer.status()
        assert status["embeddings_enabled"] is True
        assert status["embedded"] > 0
    finally:
        await _set("iq_embeddings_enabled", False)


# -- ask --------------------------------------------------------------------


async def test_ask_without_matches_skips_the_model() -> None:
    result = await ask("zzqq nothing indexed matches this")
    assert result.sources == []
    assert "couldn't find" in result.answer


async def test_ask_grounds_on_retrieved_sources() -> None:
    await _topic("Quokka migration plan", messages=("quokka cutover is scheduled for friday",))
    result = await ask("When is the quokka cutover?")
    assert result.sources
    assert result.model
    assert result.answer


# -- MCP --------------------------------------------------------------------


async def test_mcp_retrieve_and_ask_are_gated() -> None:
    await _set("mcp_expose", {})
    assert "not exposed" in (await ps.retrieve("anything"))["error"]
    assert "not exposed" in (await ps.ask("anything"))["error"]


async def test_mcp_retrieve_only_returns_exposed_sections() -> None:
    tid = await _topic("Mcpwidget topic", messages=("mcpwidget private discussion",))
    await _set("mcp_expose", {"iq": True, "topics": True})
    try:
        out = await ps.retrieve("mcpwidget")
        assert out["count"] >= 1
        assert all(h["source_kind"] != "message" for h in out["hits"])
        assert any(h["entity_id"] == tid for h in out["hits"])
        assert out["markdown"].startswith("Precursor results")
        await _set("mcp_expose", {"iq": True, "topics": True, "messages": True})
        out = await ps.retrieve("mcpwidget private", sources=["messages"])
        assert out["hits"] and all(h["source_kind"] == "message" for h in out["hits"])
        assert "error" in await ps.retrieve("mcpwidget", sources=["nope"])
    finally:
        await _set("mcp_expose", {})


# -- REST -------------------------------------------------------------------


def test_rest_endpoints_round_trip() -> None:
    app = create_app()
    with TestClient(app) as client:
        topic = client.post("/api/topics", json={"title": "Restful vorpal notes"}).json()
        # Retrieval only drains what fits its time budget, oldest first; a slow
        # runner with an earlier test's backlog may not reach this topic.
        client.portal.call(_indexed)
        r = client.get("/api/iq/retrieve", params={"q": "vorpal", "sections": "topics,chats"})
        assert r.status_code == 200
        body = r.json()
        assert any(h["entity_id"] == topic["id"] for h in body["hits"])
        assert {"markdown", "lexical_backend", "semantic", "pending"} <= set(body)

        assert (
            client.get("/api/iq/retrieve", params={"q": "x", "sections": "bogus"}).status_code
            == 422
        )

        asked = client.post("/api/iq/ask", json={"question": "What are the vorpal notes?"})
        assert asked.status_code == 200
        assert asked.json()["sources"]

        status = client.get("/api/iq/status").json()
        assert status["enabled"] is True
        assert status["chunks"] > 0

        assert client.post("/api/iq/reindex").status_code == 200

        settings = client.get("/api/settings").json()
        assert settings["iq_embeddings_enabled"] is False
        assert settings["iq_embedding_model"] == "text-embedding-3-small"
        assert settings["mcp_expose"]["iq"] is False


def test_any_process_using_the_db_module_queues_changes() -> None:
    """Scripts and the stdio MCP server write without importing the IQ package."""
    import subprocess
    import sys

    code = (
        "import precursor.backend.db\n"
        "from sqlalchemy import event\n"
        "from sqlalchemy.orm import Session\n"
        "from precursor.backend.services.iq import events\n"
        "assert event.contains(Session, 'after_flush', events._after_flush)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


async def test_title_only_chunk_reports_its_title_and_short_words_match_whole() -> None:
    async with SessionLocal() as session:
        session.add(MeetingSession(title="Vexillology review", slug="iq-vexillology-review"))
        await session.commit()
    await _topic("Eurozone plans", description="Europe expansion for the frobwidget")
    hits = (await retrieve("vexillology frobwidget")).hits
    live = next(h for h in hits if h.section == "live")
    assert live.field == "title"
    assert live.snippet == "Vexillology review"
    # Three-letter words are whole-word matches, not prefixes.
    assert not (await retrieve("eur")).hits


def test_question_words_are_not_search_terms() -> None:
    assert query_tokens("What did we decide about the latency regression?") == [
        "decide",
        "latency",
        "regression",
    ]
    assert query_tokens("Quand est prévue la première vague ?") == ["prevue", "premiere", "vague"]
