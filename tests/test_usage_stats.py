"""Tests for global usage statistics — ledger aggregation, recording, endpoint.

Usage stats read from the ``usage_records`` ledger, which captures every
metered LLM round-trip (chat turns AND utility commands like ``/notes``), so
utility calls that persist no conversation message are still counted.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import Topic, UsageRecord
from precursor.backend.services.usage_stats import compute_usage_stats, record_usage


def _init_db() -> None:
    # Lifespan runs init_db (alembic upgrade head) before we touch the tables.
    with TestClient(create_app()):
        pass


async def _ledger_count() -> int:
    async with SessionLocal() as session:
        return int(await session.scalar(select(func.count()).select_from(UsageRecord)) or 0)


async def _seed_ledger() -> None:
    async with SessionLocal() as session:
        await session.execute(delete(UsageRecord))
        session.add_all(
            [
                UsageRecord(
                    source="chat",
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    created_at=datetime(2025, 1, 6, 12, 0, tzinfo=UTC),
                ),
                UsageRecord(
                    source="chat",
                    prompt_tokens=20,
                    completion_tokens=10,
                    total_tokens=30,
                    created_at=datetime(2025, 1, 7, 12, 0, tzinfo=UTC),
                ),
                UsageRecord(
                    source="/notes rephrase",
                    prompt_tokens=100,
                    completion_tokens=50,
                    total_tokens=150,
                    created_at=datetime(2025, 2, 3, 12, 0, tzinfo=UTC),
                ),
            ]
        )
        await session.commit()


async def test_compute_usage_stats_buckets_and_totals() -> None:
    _init_db()
    await _seed_ledger()

    async with SessionLocal() as session:
        stats = await compute_usage_stats(session)

    assert stats.totals.prompt_tokens == 130
    assert stats.totals.completion_tokens == 65
    assert stats.totals.total_tokens == 195
    assert stats.totals.message_count == 3

    yearly = {b.period: b for b in stats.yearly}
    assert yearly["2025"].total_tokens == 195

    monthly = {b.period: b for b in stats.monthly}
    assert monthly["2025-01"].total_tokens == 45
    assert monthly["2025-02"].total_tokens == 150

    # Jan 6 (Mon) and Jan 7 (Tue) 2025 fall in ISO week 2.
    weekly = {b.period: b for b in stats.weekly}
    assert weekly["2025-W02"].total_tokens == 45


async def test_record_usage_skips_zero_token_rounds() -> None:
    _init_db()
    before = await _ledger_count()
    async with SessionLocal() as session:
        await record_usage(session, prompt_tokens=0, completion_tokens=0)
        await session.commit()
    assert await _ledger_count() == before


async def test_notes_rephrase_records_usage(monkeypatch) -> None:
    """A utility command (no Message persisted) still lands in the ledger.

    Force the MockProvider so the test is hermetic; it surfaces a UsageEvent,
    so the command records a ledger row.
    """
    from precursor.backend.routers import commands as commands_router
    from precursor.backend.services.llm.mock import MockProvider

    async def _mock_provider(_session, **_kwargs) -> MockProvider:
        return MockProvider()

    monkeypatch.setattr(commands_router, "get_llm_provider", _mock_provider)

    _init_db()
    async with SessionLocal() as session:
        topic = Topic(title="Notes", slug="notes-usage-cmd")
        session.add(topic)
        await session.commit()
        await session.refresh(topic)
        topic_id = topic.id

    before = await _ledger_count()

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/topics/{topic_id}/commands/notes/rephrase",
            json={"text": "rough meeting notes: ship the thing", "instruction": None},
        )
    assert resp.status_code == 200

    after = await _ledger_count()
    assert after == before + 1

    async with SessionLocal() as session:
        latest = (
            await session.execute(select(UsageRecord).order_by(UsageRecord.id.desc()).limit(1))
        ).scalar_one()
    assert latest.source == "/notes rephrase"
    assert latest.topic_id == topic_id
    assert latest.total_tokens > 0


def test_usage_stats_endpoint() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/stats/usage")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"totals", "weekly", "monthly", "yearly"}
        assert body["totals"]["total_tokens"] >= 0
        assert isinstance(body["weekly"], list)
