"""Multi-rule recurrence tests.

A schedule may carry several "when to run" rules at once — "every day at 07:00"
*plus* "every weekday at 12:00" — and must fire at whichever comes first. These
cover the timing maths, the storage round-trip on all three schedulable owners
(topic schedule, agent schedule, workflow), and that the legacy single-rule
payloads still behave exactly as before.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.models.topic_schedule import TopicSchedule
from precursor.backend.services.schedule_timing import (
    MAX_RULES,
    RecurrenceRule,
    compute_next_run,
    compute_next_run_multi,
    normalize_rules,
    rules_from_json,
    rules_to_json,
)

# Monday 2026-06-15, 09:00 UTC.
MONDAY_9AM = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)
WEEKDAYS_MASK = 0b0011111  # Mon..Fri


# --------------------------------------------------------------------- timing


def test_multi_rule_picks_the_earliest_next_occurrence() -> None:
    daily_7am = RecurrenceRule(run_at_minute=7 * 60)
    weekday_noon = RecurrenceRule(run_at_minute=12 * 60, days_of_week=WEEKDAYS_MASK)

    # From Monday 09:00 the noon rule wins (07:00 already passed today).
    assert compute_next_run_multi(MONDAY_9AM, [daily_7am, weekday_noon]) == datetime(
        2026, 6, 15, 12, 0, tzinfo=UTC
    )
    # Order of the rules must not matter.
    assert compute_next_run_multi(MONDAY_9AM, [weekday_noon, daily_7am]) == datetime(
        2026, 6, 15, 12, 0, tzinfo=UTC
    )
    # From Monday 13:00 the next slot is tomorrow's 07:00.
    after_noon = MONDAY_9AM.replace(hour=13)
    assert compute_next_run_multi(after_noon, [daily_7am, weekday_noon]) == datetime(
        2026, 6, 16, 7, 0, tzinfo=UTC
    )


def test_multi_rule_skips_a_days_restricted_rule_over_the_weekend() -> None:
    daily_7am = RecurrenceRule(run_at_minute=7 * 60)
    weekday_noon = RecurrenceRule(run_at_minute=12 * 60, days_of_week=WEEKDAYS_MASK)
    # Saturday 13:00: the weekday rule jumps to Monday, so Sunday 07:00 wins.
    saturday = datetime(2026, 6, 20, 13, 0, tzinfo=UTC)
    assert compute_next_run_multi(saturday, [daily_7am, weekday_noon]) == datetime(
        2026, 6, 21, 7, 0, tzinfo=UTC
    )


def test_single_rule_matches_the_legacy_computation() -> None:
    rule = RecurrenceRule(interval_seconds=3600, days_of_week=WEEKDAYS_MASK)
    assert compute_next_run_multi(MONDAY_9AM, [rule]) == compute_next_run(
        MONDAY_9AM, 3600, WEEKDAYS_MASK, None, "UTC"
    )


def test_empty_rule_set_has_no_next_run() -> None:
    assert compute_next_run_multi(MONDAY_9AM, []) is None


# ---------------------------------------------------------------- normalising


def test_normalize_clamps_and_deduplicates() -> None:
    rules = normalize_rules(
        [
            RecurrenceRule(interval_seconds=5),  # below the 60s floor
            RecurrenceRule(interval_seconds=5),  # duplicate of the clamped one
            RecurrenceRule(days_of_week=999, run_at_minute=9999),
        ]
    )
    assert [r.interval_seconds for r in rules] == [60, 86400]
    assert rules[1].days_of_week == 127
    assert rules[1].run_at_minute == 24 * 60 - 1


def test_normalize_caps_the_rule_count() -> None:
    many = [RecurrenceRule(run_at_minute=minute) for minute in range(MAX_RULES + 10)]
    assert len(normalize_rules(many)) == MAX_RULES


def test_rules_json_round_trip_and_malformed_input() -> None:
    rules = [RecurrenceRule(run_at_minute=420), RecurrenceRule(interval_seconds=7200)]
    assert rules_from_json(rules_to_json(rules)) == rules
    assert rules_to_json([]) is None
    # Anything unparseable degrades to "no extras" rather than exploding.
    for junk in (None, "", "not json", '{"not": "a list"}', "[1, 2, 3]"):
        assert rules_from_json(junk) == []


# ------------------------------------------------------------- model plumbing


def test_schedule_model_splits_primary_and_extra_rules() -> None:
    schedule = TopicSchedule(topic_id=1, prompt="x", interval_seconds=60)
    schedule.set_recurrence_rules(
        [
            RecurrenceRule(run_at_minute=7 * 60),
            RecurrenceRule(run_at_minute=12 * 60, days_of_week=WEEKDAYS_MASK),
        ]
    )
    # The primary rule lands in the flat columns; the rest are JSON-encoded.
    assert schedule.run_at_minute == 7 * 60
    assert schedule.days_of_week == 127
    assert schedule.extra_rules is not None
    assert len(schedule.recurrence_rules) == 2
    assert schedule.next_run_after(MONDAY_9AM) == datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def test_schedule_model_without_extras_reads_back_as_one_rule() -> None:
    schedule = TopicSchedule(topic_id=1, prompt="x", interval_seconds=3600, days_of_week=127)
    assert schedule.extra_rules is None
    assert schedule.recurrence_rules == [RecurrenceRule(interval_seconds=3600)]


# ------------------------------------------------------------------- topic API


def _make_topic(client: TestClient, title: str = "Digest") -> int:
    created = client.post("/api/topics", json={"title": title})
    assert created.status_code in (200, 201)
    return created.json()["id"]


def test_topic_schedule_accepts_and_returns_multiple_rules() -> None:
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)
        created = client.post(
            f"/api/topics/{topic_id}/schedule",
            json={
                "prompt": "Morning + midday digest",
                "rules": [
                    {"run_at_minute": 420, "timezone": "UTC"},
                    {"run_at_minute": 720, "days_of_week": WEEKDAYS_MASK, "timezone": "UTC"},
                ],
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert [r["run_at_minute"] for r in body["rules"]] == [420, 720]
        # The primary rule stays mirrored on the flat fields.
        assert body["run_at_minute"] == 420
        assert body["next_run_at"] is not None

        # The rule set survives a plain read and the embedded topic summary.
        fetched = client.get(f"/api/topics/{topic_id}/schedule").json()
        assert len(fetched["rules"]) == 2
        summary = client.get(f"/api/topics/{topic_id}").json()["schedule"]
        assert [r["run_at_minute"] for r in summary["rules"]] == [420, 720]


def test_topic_schedule_flat_patch_keeps_extra_rules() -> None:
    """An old client PATCHing only the interval must not drop the extras."""
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)
        client.post(
            f"/api/topics/{topic_id}/schedule",
            json={
                "prompt": "p",
                "rules": [{"run_at_minute": 420}, {"run_at_minute": 720}],
            },
        )
        patched = client.patch(
            f"/api/topics/{topic_id}/schedule", json={"days_of_week": WEEKDAYS_MASK}
        ).json()
        assert len(patched["rules"]) == 2
        # Only the primary rule was patched.
        assert patched["rules"][0]["days_of_week"] == WEEKDAYS_MASK
        assert patched["rules"][1]["days_of_week"] == 127


def test_topic_schedule_rules_replace_the_whole_set() -> None:
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)
        client.post(
            f"/api/topics/{topic_id}/schedule",
            json={"prompt": "p", "rules": [{"run_at_minute": 420}, {"run_at_minute": 720}]},
        )
        patched = client.patch(
            f"/api/topics/{topic_id}/schedule",
            json={"rules": [{"interval_seconds": 3600}]},
        ).json()
        assert len(patched["rules"]) == 1
        assert patched["interval_seconds"] == 3600
        assert patched["run_at_minute"] is None


def test_topic_schedule_rejects_an_empty_rule_set() -> None:
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)
        assert (
            client.post(
                f"/api/topics/{topic_id}/schedule", json={"prompt": "p", "rules": []}
            ).status_code
            == 422
        )
        # A payload with no cadence at all is equally rejected.
        assert (
            client.post(f"/api/topics/{topic_id}/schedule", json={"prompt": "p"}).status_code == 422
        )


def test_legacy_single_rule_payload_still_works() -> None:
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)
        body = client.post(
            f"/api/topics/{topic_id}/schedule",
            json={"prompt": "p", "interval_seconds": 900, "days_of_week": WEEKDAYS_MASK},
        ).json()
        assert body["interval_seconds"] == 900
        assert body["rules"] == [
            {
                "interval_seconds": 900,
                "days_of_week": WEEKDAYS_MASK,
                "run_at_minute": None,
                "timezone": "UTC",
            }
        ]


# ------------------------------------------------------------------ agent API


async def _make_agent() -> int:
    """Insert an AgentSession directly (POST /api/agents needs the runtime)."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    async with SessionLocal() as session:
        agent = AgentSession(title="Reporter", task_prompt="Report", status="idle")
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        return agent.id


def test_agent_schedule_accepts_multiple_rules() -> None:
    app = create_app()
    with TestClient(app) as client:
        agent_id = asyncio.run(_make_agent())
        created = client.post(
            f"/api/agents/{agent_id}/schedule",
            json={
                "rules": [
                    {"run_at_minute": 420},
                    {"run_at_minute": 720, "days_of_week": WEEKDAYS_MASK},
                ]
            },
        )
        assert created.status_code == 201
        assert [r["run_at_minute"] for r in created.json()["rules"]] == [420, 720]

        embedded = client.get(f"/api/agents/{agent_id}").json()["schedule"]
        assert len(embedded["rules"]) == 2


# --------------------------------------------------------------- workflow API


def test_workflow_schedule_accepts_multiple_rules_and_clears_daily_mode() -> None:
    app = create_app()
    with TestClient(app) as client:
        # Workflows live behind Agents mode.
        assert client.put("/api/settings", json={"agents_enabled": True}).status_code == 200
        created = client.post("/api/workflows", json={"name": "Nightly", "steps": []})
        assert created.status_code in (200, 201), created.text
        workflow_id = created.json()["id"]

        body = client.put(
            f"/api/workflows/{workflow_id}/schedule",
            json={
                "schedule_enabled": True,
                "rules": [
                    {"run_at_minute": 420},
                    {"run_at_minute": 720, "days_of_week": WEEKDAYS_MASK},
                ],
            },
        ).json()
        assert [r["run_at_minute"] for r in body["rules"]] == [420, 720]
        assert body["next_run_at"] is not None

        # Switching back to a pure interval clears the daily time rather than
        # leaving the workflow stuck on its old "at a time" cadence.
        body = client.put(
            f"/api/workflows/{workflow_id}/schedule",
            json={
                "schedule_enabled": True,
                "interval_seconds": 3600,
                "run_at_minute": None,
                "days_of_week": 127,
                "timezone": "UTC",
            },
        ).json()
        assert body["run_at_minute"] is None
        assert body["interval_seconds"] == 3600
