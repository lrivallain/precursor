"""Tests for re-capping already-archived agent event payloads.

Rows written before a cap existed keep their original size forever: retention
can't help because an oversized payload isn't necessarily old, and it counts as
one row against a per-session ceiling however many KB it holds. Covers: the pure
transform (over-cap values trimmed, compliant ones left byte-identical, unknown
fields preserved), and the sweep itself shrinking rows in place while keeping
every event parseable as a timeline node.
"""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AgentEventRecord, AgentSession
from precursor.backend.schemas.agent import AgentEvent
from precursor.backend.services.agent_event_recap import recap_archived_events, recap_payload
from precursor.backend.services.agents.event_normalizer import (
    SYSTEM_TEXT_CAP,
    TEXT_CAP,
    TOOL_RESULT_CAP,
)


def _init_db() -> None:
    with TestClient(create_app()):
        pass


def test_recap_trims_oversized_data_values() -> None:
    payload = json.dumps({"kind": "HookStartData", "data": {"input": "x" * 50_000}})

    out = recap_payload(payload)

    assert out is not None
    assert len(json.loads(out)["data"]["input"]) <= TOOL_RESULT_CAP


def test_recap_applies_the_tighter_system_prompt_cap() -> None:
    payload = json.dumps({"kind": "SystemMessageData", "text": "s" * 50_000})

    out = recap_payload(payload)

    assert out is not None
    assert len(json.loads(out)["text"]) <= SYSTEM_TEXT_CAP


def test_recap_leaves_compliant_payloads_alone() -> None:
    """A payload already within the caps is reported as unchanged.

    The sweep relies on this to skip rows rather than rewriting the whole table.
    """
    payload = json.dumps({"kind": "assistant_message", "text": "a" * 100})

    assert recap_payload(payload) is None


def test_recap_preserves_unknown_fields_and_non_strings() -> None:
    """Only captured strings are trimmed; structure and metrics survive intact."""
    payload = json.dumps(
        {
            "kind": "usage",
            "text": None,
            "tool_name": "fetch",
            "future_field": {"nested": True},
            "data": {"input_tokens": 1234, "success": False, "result": "r" * 50_000},
        }
    )

    out = recap_payload(payload)

    assert out is not None
    parsed = json.loads(out)
    assert parsed["tool_name"] == "fetch"
    assert parsed["future_field"] == {"nested": True}
    assert parsed["data"]["input_tokens"] == 1234
    assert parsed["data"]["success"] is False
    assert len(parsed["data"]["result"]) <= TOOL_RESULT_CAP


def test_recap_ignores_malformed_payloads() -> None:
    assert recap_payload("not json at all") is None


def _seed() -> None:
    async def _run() -> None:
        async with SessionLocal() as session:
            await session.execute(delete(AgentEventRecord))
            await session.execute(delete(AgentSession))
            agent = AgentSession(title="A", task_prompt="x", status="completed")
            session.add(agent)
            await session.flush()
            session.add_all(
                [
                    AgentEventRecord(
                        agent_session_id=agent.id,
                        payload=json.dumps(
                            {"kind": "HookStartData", "data": {"input": "x" * 50_000}}
                        ),
                    ),
                    AgentEventRecord(
                        agent_session_id=agent.id,
                        payload=json.dumps({"kind": "assistant_message", "text": "small"}),
                    ),
                ]
            )
            await session.commit()

    asyncio.run(_run())


async def _total_payload_bytes() -> int:
    async with SessionLocal() as session:
        return int(
            (
                await session.execute(
                    select(func.coalesce(func.sum(func.length(AgentEventRecord.payload)), 0))
                )
            ).scalar_one()
        )


def test_sweep_shrinks_rows_and_keeps_them_parseable() -> None:
    _init_db()
    _seed()

    async def _run() -> None:
        before = await _total_payload_bytes()

        result = await recap_archived_events()

        # Only the oversized row is rewritten; the small one is skipped.
        assert result.rows == 1
        assert result.bytes > 0
        after = await _total_payload_bytes()
        assert after < before
        assert before - after == result.bytes

        # Every surviving row must still deserialise — the archive is replayed
        # through this model to rebuild an agent's timeline after a restart.
        async with SessionLocal() as session:
            payloads = (await session.execute(select(AgentEventRecord.payload))).scalars().all()
        assert len(payloads) == 2
        for payload in payloads:
            event = AgentEvent.model_validate_json(payload)
            assert event.kind
            if event.text is not None:
                assert len(event.text) <= TEXT_CAP

    asyncio.run(_run())


def test_sweep_is_idempotent() -> None:
    _init_db()
    _seed()

    async def _run() -> None:
        first = await recap_archived_events()
        assert first.rows == 1
        # Nothing left over the cap, so a second pass is a no-op.
        assert (await recap_archived_events()).rows == 0

    asyncio.run(_run())


def test_dry_run_measures_without_rewriting() -> None:
    _init_db()
    _seed()

    async def _run() -> None:
        before = await _total_payload_bytes()

        preview = await recap_archived_events(dry_run=True)

        assert preview.rows == 1
        assert preview.bytes > 0
        assert await _total_payload_bytes() == before

    asyncio.run(_run())
