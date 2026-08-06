from __future__ import annotations

import anyio
from sqlalchemy import select

from precursor.backend.db import SessionLocal
from precursor.backend.models.collection import Collection
from precursor.backend.models.meeting import MeetingSession
from precursor.backend.models.topic import Topic
from precursor.backend.services.demo_data import seed_collection_scope_demo


def test_seed_collection_scope_demo_creates_demo_fixture() -> None:
    result = anyio.run(seed_collection_scope_demo)

    assert result["collections"]["platform"] > 0
    assert result["collections"]["operations"] > 0
    assert result["topics"]["platform_root"] > 0
    assert result["topics"]["platform_child"] > 0
    assert result["topics"]["operations_root"] > 0
    assert result["topics"]["operations_child"] > 0

    async def read_fixture() -> tuple[Collection, Topic, MeetingSession]:
        async with SessionLocal() as session:
            topic = await session.get(Topic, result["topics"]["operations_root"])
            assert topic is not None
            collection = await session.get(Collection, result["collections"]["operations"])
            assert collection is not None
            meetings = (await session.execute(select(MeetingSession))).scalars().all()
            meeting = next(m for m in meetings if m.title == "Collection scope demo")
            return collection, topic, meeting

    collection, topic, meeting = anyio.run(read_fixture)

    assert collection.name == "Demo - Operations"
    assert topic.title == "Incident handoff"
    assert meeting.topic_id == topic.id
