"""Agent schedule tests — recurrence attached to an agent session.

Mirror of the scheduled-topics tests, for the agent variant: the HTTP CRUD
surface (which is runtime-independent) plus the scheduler worker that triggers
an agent's re-run. The live Copilot SDK can't run here, so the trigger path is
exercised against a stub agent manager.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app


@pytest.fixture(autouse=True)
def _cleanup_agents() -> object:
    """Wipe agent rows after each test so direct inserts don't leak into the
    session-shared DB (e.g. ``test_agents_disabled_by_default`` asserts empty)."""
    yield

    async def _wipe() -> None:
        from sqlalchemy import delete

        from precursor.backend.db import SessionLocal
        from precursor.backend.models import AgentSchedule, AgentSession

        async with SessionLocal() as session:
            await session.execute(delete(AgentSchedule))
            await session.execute(delete(AgentSession))
            await session.commit()

    asyncio.run(_wipe())


async def _make_agent(*, task: str = "check the inbox", status: str = "idle") -> int:
    """Insert an AgentSession directly (POST /api/agents needs the runtime)."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    async with SessionLocal() as session:
        agent = AgentSession(title="Test agent", task_prompt=task, status=status)
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        return agent.id


def test_agent_schedule_crud() -> None:
    app = create_app()
    with TestClient(app) as client:
        agent_id = asyncio.run(_make_agent())

        # No schedule yet.
        assert client.get(f"/api/agents/{agent_id}/schedule").status_code == 404
        assert client.get(f"/api/agents/{agent_id}").json()["schedule"] is None

        created = client.post(
            f"/api/agents/{agent_id}/schedule",
            json={"interval_seconds": 300, "clear_context": True},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["enabled"] is True
        assert body["clear_context"] is True
        assert body["next_run_at"] is not None
        assert body["agent_session_id"] == agent_id

        # Embedded in the agent read.
        embedded = client.get(f"/api/agents/{agent_id}").json()["schedule"]
        assert embedded is not None and embedded["interval_seconds"] == 300

        # Creating again conflicts.
        assert (
            client.post(
                f"/api/agents/{agent_id}/schedule", json={"interval_seconds": 600}
            ).status_code
            == 409
        )

        # Pause: enabled=false clears the next run.
        paused = client.patch(f"/api/agents/{agent_id}/schedule", json={"enabled": False}).json()
        assert paused["enabled"] is False
        assert paused["next_run_at"] is None

        # Delete removes the row but keeps the agent.
        assert client.delete(f"/api/agents/{agent_id}/schedule").status_code == 204
        assert client.get(f"/api/agents/{agent_id}/schedule").status_code == 404
        assert client.get(f"/api/agents/{agent_id}").status_code == 200


def test_agent_schedule_run_now_pulls_forward() -> None:
    app = create_app()
    with TestClient(app) as client:
        agent_id = asyncio.run(_make_agent())
        client.post(
            f"/api/agents/{agent_id}/schedule",
            json={"interval_seconds": 86400},  # tomorrow, normally
        )
        ran = client.post(f"/api/agents/{agent_id}/schedule/run")
        assert ran.status_code == 200
        body = ran.json()
        assert body["enabled"] is True
        assert body["status"] == "idle"
        assert body["next_run_at"] is not None


def _async_true(*_args: object, **_kwargs: object) -> object:
    async def _inner() -> bool:
        return True

    return _inner()


class _StubManager:
    ready = True

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def rerun_task(self, agent_id: int) -> None:
        self.calls.append(("rerun", agent_id))

    async def send_message(self, agent_id: int, text: str) -> None:
        self.calls.append(("send", agent_id, text))


@pytest.mark.parametrize(
    ("clear_context", "expected"),
    [(True, "rerun"), (False, "send")],
)
def test_scheduler_triggers_agent_run(
    monkeypatch: pytest.MonkeyPatch, clear_context: bool, expected: str
) -> None:
    """`_run_one_agent` fires the manager and advances next_run_at."""
    from precursor.backend.services import scheduler as scheduler_mod
    from precursor.backend.services.agents import manager as manager_mod
    from precursor.backend.services.agents import runtime as agent_runtime

    app = create_app()
    with TestClient(app) as client:
        agent_id = asyncio.run(_make_agent(task="summarise inbox"))
        client.post(
            f"/api/agents/{agent_id}/schedule",
            json={"interval_seconds": 300, "clear_context": clear_context},
        )

        stub = _StubManager()
        monkeypatch.setattr(scheduler_mod, "resolve_agents_enabled", _async_true)
        monkeypatch.setattr(agent_runtime, "agents_available", lambda: (True, "ok"))
        monkeypatch.setattr(manager_mod, "get_agent_manager", lambda: stub)

        sched = scheduler_mod.Scheduler()
        asyncio.run(sched._run_one_agent(agent_id))

        assert len(stub.calls) == 1
        assert stub.calls[0][0] == expected
        if expected == "send":
            assert stub.calls[0][2] == "summarise inbox"

        schedule = client.get(f"/api/agents/{agent_id}/schedule").json()
        assert schedule["status"] == "ok"
        assert schedule["last_run_at"] is not None
        assert schedule["next_run_at"] is not None


def test_scheduler_skips_agent_when_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run in flight is skipped (not errored) and recorded as such."""
    from precursor.backend.services import scheduler as scheduler_mod
    from precursor.backend.services.agents import manager as manager_mod
    from precursor.backend.services.agents import runtime as agent_runtime

    app = create_app()
    with TestClient(app) as client:
        agent_id = asyncio.run(_make_agent(status="running"))
        client.post(f"/api/agents/{agent_id}/schedule", json={"interval_seconds": 300})

        stub = _StubManager()
        monkeypatch.setattr(scheduler_mod, "resolve_agents_enabled", _async_true)
        monkeypatch.setattr(agent_runtime, "agents_available", lambda: (True, "ok"))
        monkeypatch.setattr(manager_mod, "get_agent_manager", lambda: stub)

        sched = scheduler_mod.Scheduler()
        asyncio.run(sched._run_one_agent(agent_id))

        assert stub.calls == []  # nothing triggered
        schedule = client.get(f"/api/agents/{agent_id}/schedule").json()
        assert schedule["status"] == "ok"  # skip is not a failure
        assert "in progress" in (schedule["last_error"] or "")
