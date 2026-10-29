"""Side-chat demo fixture: a topic with replies, and chats started from it.

Imported by ``seed_demo.py`` for the screenshots, and runnable on its own to add
the fixture to a development database::

    UV_NO_SYNC=1 uv run --frozen python scripts/demo_side_chats.py

Re-running replaces the fixture. It refuses to touch the installed app's
database (the one under the user data directory).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from precursor.backend.models import (  # noqa: E402
    Chat,
    Collection,
    Message,
    MessageRole,
    Reminder,
    Topic,
    TopicSummary,
)

TOPIC_SLUG = "billing-db-migration"

_TURNS = [
    (
        "What's the safest cutover strategy for moving the billing database from "
        "PostgreSQL 13 to 17?",
        "**Logical replication with a short write freeze** is the safest route here.\n\n"
        "1. Stand up the PostgreSQL 17 cluster and load the schema with `pg_dump --schema-only`.\n"
        "2. Create a publication on 13 and a subscription on 17; let the initial copy finish.\n"
        "3. Watch `pg_stat_subscription` until replication lag stays under a second.\n"
        "4. Freeze writes, wait for lag to hit zero, sync sequences, and flip the "
        "connection string.\n\n"
        "Compared with dump/restore, the downtime is minutes instead of hours, and "
        "the old cluster stays intact as a fallback until you decommission it.",
    ),
    (
        "How long would the write freeze last?",
        "Roughly **4 to 6 minutes**, dominated by the checks rather than the copy:\n\n"
        "| Step | Time |\n|---|---|\n"
        "| Drain in-flight invoices | ~1 min |\n"
        "| Wait for lag to reach zero | < 30 s |\n"
        "| Sync sequences (`setval`) | ~30 s |\n"
        "| Smoke tests on 17 | ~2 min |\n"
        "| Flip config + restart workers | ~1 min |\n\n"
        "Schedule it in the Sunday 06:00 CET window, when invoice traffic is lowest.",
    ),
    (
        "Draft the rollback plan.",
        "**Rollback plan**, valid for 48 hours after cutover:\n\n"
        "1. Keep the 13 cluster read-only, not deleted.\n"
        "2. Set up reverse logical replication (17 → 13) right after cutover, so 13 "
        "stays current.\n"
        "3. To roll back: freeze writes, confirm reverse lag is zero, flip the "
        "connection string back, unfreeze.\n"
        "4. Decision owner: the on-call billing lead. Trigger: error rate above 1% "
        "for 10 minutes, or any ledger mismatch.\n\n"
        "Rehearse it once on staging before the real window.",
    ),
]

_SUMMARY = (
    "## Status\n"
    "- Strategy agreed: logical replication with a 4 to 6 minute write freeze.\n"
    "- Rollback plan drafted; staging rehearsal not done yet.\n\n"
    "## Actions\n"
    "- [ ] Rehearse the rollback on staging\n"
    "- [ ] Announce the Sunday 06:00 CET window\n"
    "- [x] Size the PostgreSQL 17 cluster\n\n"
    "## Key information\n"
    "- Rollback window: 48 h after cutover\n"
    "- Owner: on-call billing lead"
)


def _ago(now: datetime, **kw: float) -> datetime:
    return now - timedelta(**kw)


async def _clear(s: AsyncSession) -> None:
    topic = (await s.execute(select(Topic).where(Topic.slug == TOPIC_SLUG))).scalar_one_or_none()
    if topic is None:
        return
    chat_ids = list(
        (await s.execute(select(Chat.id).where(Chat.parent_topic_id == topic.id))).scalars()
    )
    if chat_ids:
        await s.execute(delete(Reminder).where(Reminder.chat_id.in_(chat_ids)))
        await s.execute(delete(Message).where(Message.chat_id.in_(chat_ids)))
        await s.execute(delete(Chat).where(Chat.id.in_(chat_ids)))
    await s.execute(delete(TopicSummary).where(TopicSummary.topic_id == topic.id))
    await s.execute(delete(Message).where(Message.topic_id == topic.id))
    await s.delete(topic)
    await s.flush()


async def _chat_turn(
    s: AsyncSession, chat: Chat, prompt: str, answer: str, at: datetime
) -> Message:
    s.add(Message(chat_id=chat.id, role=MessageRole.USER, content=prompt, created_at=at))
    reply = Message(
        chat_id=chat.id,
        role=MessageRole.ASSISTANT,
        content=answer,
        prompt_tokens=1400,
        completion_tokens=180,
        model="mock",
        created_at=at + timedelta(seconds=6),
    )
    s.add(reply)
    await s.flush()
    return reply


async def seed_side_chats(s: AsyncSession, collection_id: int | None = None) -> Topic:
    """Add (or replace) the fixture. Doesn't commit."""
    now = datetime.now(UTC)
    await _clear(s)
    if collection_id is None:
        collection_id = (
            await s.execute(select(Collection.id).where(Collection.is_default.is_(True)))
        ).scalar_one_or_none()

    topic = Topic(
        title="Billing DB migration to PostgreSQL 17",
        slug=TOPIC_SLUG,
        description="Move the billing database off PostgreSQL 13 with minimal downtime.",
        collection_id=collection_id,
        last_read_at=now,
    )
    s.add(topic)
    await s.flush()

    replies: list[Message] = []
    for i, (prompt, answer) in enumerate(_TURNS):
        at = _ago(now, hours=6 - i * 1.5)
        s.add(Message(topic_id=topic.id, role=MessageRole.USER, content=prompt, created_at=at))
        reply = Message(
            topic_id=topic.id,
            role=MessageRole.ASSISTANT,
            content=answer,
            prompt_tokens=1100 + 300 * i,
            completion_tokens=220,
            model="mock",
            created_at=at + timedelta(seconds=8),
        )
        s.add(reply)
        await s.flush()
        replies.append(reply)

    s.add(
        TopicSummary(
            topic_id=topic.id, content=_SUMMARY, visible=True, model="mock", generated_at=now
        )
    )

    # From the first reply, with a follow-up the user hasn't read yet.
    gotchas = Chat(
        title="Logical replication gotchas",
        slug="logical-replication-gotchas",
        parent_topic_id=topic.id,
        parent_message_id=replies[0].id,
        seed_content=replies[0].content,
        created_at=_ago(now, hours=3),
        last_read_at=_ago(now, hours=2),
    )
    # On the whole topic, with a reminder to send the announcement.
    comms = Chat(
        title="Maintenance window announcement",
        slug="maintenance-window-announcement",
        parent_topic_id=topic.id,
        created_at=_ago(now, hours=2),
        last_read_at=now,
    )
    # From the rollback reply, just opened.
    rehearsal = Chat(
        title="Rollback rehearsal checklist",
        slug="rollback-rehearsal-checklist",
        parent_topic_id=topic.id,
        parent_message_id=replies[2].id,
        seed_content=replies[2].content,
        created_at=_ago(now, minutes=20),
        last_read_at=now,
    )
    s.add_all([gotchas, comms, rehearsal])
    await s.flush()

    await _chat_turn(
        s,
        gotchas,
        "Which things does logical replication *not* carry over?",
        "Four to watch for:\n\n"
        "- **Sequences** — their values don't replicate; `setval` each one at cutover.\n"
        "- **DDL** — schema changes aren't replicated; freeze migrations until cutover.\n"
        "- **Large objects** — `pg_largeobject` isn't published; check nothing uses them.\n"
        "- **Tables without a primary key** — they need `REPLICA IDENTITY FULL`, which is "
        "slow on big tables.",
        _ago(now, hours=2.5),
    )
    await _chat_turn(
        s,
        gotchas,
        "Give me a query to find tables without a primary key.",
        "```sql\nSELECT c.relname\nFROM pg_class c\n"
        "JOIN pg_namespace n ON n.oid = c.relnamespace\n"
        "WHERE c.relkind = 'r' AND n.nspname = 'billing'\n"
        "  AND NOT EXISTS (\n"
        "    SELECT 1 FROM pg_index i WHERE i.indrelid = c.oid AND i.indisprimary\n"
        "  );\n```",
        _ago(now, minutes=40),
    )
    await _chat_turn(
        s,
        comms,
        "Draft a short announcement for the finance team.",
        "**Billing maintenance, Sunday 06:00-06:15 CET**\n\n"
        "We're upgrading the billing database. Invoices can't be created or edited "
        "for about five minutes during that window; everything else keeps working. "
        "No action needed on your side.",
        _ago(now, hours=1.5),
    )
    s.add(Reminder(chat_id=comms.id, remind_at=now + timedelta(days=1, hours=2), note="Send it"))
    await s.flush()
    return topic


async def _main() -> None:
    from precursor.backend.config import get_settings, user_data_dir
    from precursor.backend.db import SessionLocal, init_db

    url = get_settings().database_url
    if user_data_dir().as_posix() in url:
        raise SystemExit(f"Refusing to seed the installed app's database: {url}")
    await init_db()
    async with SessionLocal() as s:
        topic = await seed_side_chats(s)
        await s.commit()
    print(f"Seeded side-chat demo topic {topic.id} ({TOPIC_SLUG}) into {url}")


if __name__ == "__main__":
    asyncio.run(_main())
