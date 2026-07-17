"""Tests for the Refine with AI endpoint (/api/refine)."""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import UsageRecord


def _init_db() -> None:
    # Lifespan runs init_db (alembic upgrade head) before we touch the tables.
    with TestClient(create_app()):
        pass


async def _ledger_count() -> int:
    async with SessionLocal() as session:
        return int(await session.scalar(select(func.count()).select_from(UsageRecord)) or 0)


async def test_refine_rewrites_text_and_records_usage(monkeypatch) -> None:
    """Refining returns rewritten text and lands one row in the usage ledger.

    Force the MockProvider so the test is hermetic; it surfaces a UsageEvent,
    so the round-trip records a ledger row tagged ``/refine``.
    """
    from precursor.backend.services import text_refine
    from precursor.backend.services.llm.mock import MockProvider

    async def _mock_provider(_session, **_kwargs) -> MockProvider:
        return MockProvider()

    monkeypatch.setattr(text_refine, "get_llm_provider", _mock_provider)

    _init_db()
    before = await _ledger_count()

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/refine",
            json={"text": "make this gud pls", "kind": "note"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"].strip()
    assert body["model"]

    after = await _ledger_count()
    assert after == before + 1

    async with SessionLocal() as session:
        latest = (
            await session.execute(select(UsageRecord).order_by(UsageRecord.id.desc()).limit(1))
        ).scalar_one()
    assert latest.source == "/refine"
    assert latest.total_tokens > 0


async def test_refine_rejects_blank_text() -> None:
    _init_db()
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/refine", json={"text": "   "})
    # Pydantic min_length=1 accepts whitespace; the router guards the blank case.
    assert resp.status_code == 400
