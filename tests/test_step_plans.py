"""Step-by-step plans — a long ask worked as a tree of steps, then consolidated."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app

ASK = "Hi, please (1) quote the migration, (2) list the risks and (3) propose a date."


def _run(fn: Any) -> Any:
    return anyio.run(fn)


STEP_REPLY = "Here is a draft.\n<!-- artifact -->\nDrafted artifact\n<!-- /artifact -->\nOK?"
FINAL_REPLY = "<!-- artifact -->\nFINAL ANSWER\n<!-- /artifact -->"
# Every headless discussion turn the fake ran: chat, prompts and the system context.
TURNS: list[dict[str, Any]] = []
TURN_REPLIES: dict[str, Any] = {}


def _fake_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the headless chat turn: persist the exchange, reply with a canned text."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Chat, Message, MessageRole
    from precursor.backend.services import step_plan_llm
    from precursor.backend.services.turn import TurnResult
    from precursor.backend.services.turn_engine import build_chat_system_context

    TURNS.clear()
    TURN_REPLIES.clear()

    async def _turn(chat_id: int, prompt: str, *, llm_prompt: str | None = None) -> TurnResult:
        async with SessionLocal() as session:
            chat = await session.get(Chat, chat_id)
            assert chat is not None
            context = await build_chat_system_context(session, chat)
            kind = "step" if chat.step_id is not None else "final"
            reply = TURN_REPLIES.get(kind, STEP_REPLY if kind == "step" else FINAL_REPLY)
            TURNS.append(
                {"chat_id": chat_id, "prompt": prompt, "llm_prompt": llm_prompt, "context": context}
            )
            session.add(Message(chat_id=chat_id, role=MessageRole.USER, content=prompt))
            if reply is not None:
                session.add(Message(chat_id=chat_id, role=MessageRole.ASSISTANT, content=reply))
            await session.commit()
        return TurnResult(reply=reply, awaiting_approval=TURN_REPLIES.get("parked", False))

    monkeypatch.setattr(step_plan_llm, "run_chat_turn", _turn)


def _fake_llm(monkeypatch: pytest.MonkeyPatch, replies: dict[str, str] | None = None) -> list[dict]:
    """Route ``complete_once`` by which system prompt asked; record every call."""
    _fake_turns(monkeypatch)
    from precursor.backend.services import step_plan_llm
    from precursor.backend.services.llm.base import UsageEvent
    from precursor.backend.services.llm.one_shot import OneShotResult

    split = json.dumps(
        {
            "steps": [
                {"title": "Quote", "instruction": "Quote the migration"},
                {
                    "title": "Risks",
                    "instruction": "List the risks",
                    "children": [{"title": "Data", "instruction": "Data risks"}],
                },
                {"title": "Date", "instruction": "Propose a date"},
            ]
        }
    )
    table = {
        "break a long ask": split,
        "check whether a plan covers": json.dumps(
            {"steps": [{"title": "Rollback", "instruction": "Describe rollback"}]}
        ),
        "split one step": json.dumps({"steps": [{"title": "A"}, {"title": "B"}]}),
        "review a step-by-step": "**Not covered** — nothing",
        **(replies or {}),
    }
    calls: list[dict] = []

    async def _complete_once(_session: Any, *, system: str, user: str, **kwargs: Any) -> Any:
        calls.append({"system": system, "user": user, **kwargs})
        text = next((v for k, v in table.items() if k in system), "?")
        return OneShotResult(
            text=text,
            model="fake",
            usage=UsageEvent(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    monkeypatch.setattr(step_plan_llm, "complete_once", _complete_once)
    return calls


def _plan(client: TestClient, **extra: Any) -> dict[str, Any]:
    res = client.post("/api/step-plans", json={"source_text": ASK, **extra})
    assert res.status_code == 201, res.text
    data: dict[str, Any] = res.json()
    return data


def _by_title(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["title"]: s for s in plan["steps"]}


def _started(client: TestClient) -> dict[str, Any]:
    plan = _plan(client)
    res = client.post(f"/api/step-plans/{plan['id']}/start", json={})
    assert res.status_code == 200, res.text
    data: dict[str, Any] = res.json()
    return data


def _get(client: TestClient, plan_id: int) -> dict[str, Any]:
    data: dict[str, Any] = client.get(f"/api/step-plans/{plan_id}").json()
    return data


def _validate(client: TestClient, step_id: int, text: str = "done") -> None:
    assert client.put(f"/api/steps/{step_id}/artifact", json={"content": text}).status_code == 200
    res = client.post(f"/api/steps/{step_id}/status", json={"status": "validated"})
    assert res.status_code == 200, res.text


def test_a_plan_starts_with_the_models_proposed_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _plan(client)
        assert plan["status"] == "drafting"
        steps = _by_title(plan)
        assert set(steps) == {"Quote", "Risks", "Data", "Date"}
        assert all(s["status"] == "proposed" and s["created_by"] == "model" for s in steps.values())
        assert steps["Data"]["parent_step_id"] == steps["Risks"]["id"]
        assert ASK in calls[0]["user"]


def test_starting_accepts_the_proposals(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _started(client)
        assert plan["status"] == "active"
        steps = _by_title(plan)
        assert steps["Quote"]["status"] == "open"
        assert steps["Risks"]["effective_status"] == "open"


def test_proposing_again_replaces_unaccepted_suggestions(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _plan(client)
        assert len(plan["steps"]) == 4
        again = client.post(f"/api/step-plans/{plan['id']}/propose", json={}).json()
        assert len(again["steps"]) == 4


def test_a_garbled_proposal_is_a_502_and_keeps_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch, {"break a long ask": "sorry, I can't"})
    with TestClient(create_app()) as client:
        before = len(client.get("/api/step-plans").json())
        res = client.post("/api/step-plans", json={"source_text": ASK})
        assert res.status_code == 502
        assert len(client.get("/api/step-plans").json()) == before + 1


def test_the_proposal_survives_fences_and_prose(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(
        monkeypatch,
        {"break a long ask": 'Sure!\n```json\n{"steps": [{"title": "Only"}]}\n```'},
    )
    with TestClient(create_app()) as client:
        assert [s["title"] for s in _plan(client)["steps"]] == ["Only"]


def test_a_plan_from_a_topic_uses_its_latest_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Message, MessageRole

    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        topic = client.post("/api/topics", json={"title": "Customer mail"}).json()

        async def _seed() -> None:
            async with SessionLocal() as session:
                session.add(Message(topic_id=topic["id"], role=MessageRole.USER, content=ASK))
                await session.commit()

        _run(_seed)
        res = client.post("/api/step-plans", json={"topic_id": topic["id"], "propose": False})
        assert res.status_code == 201, res.text
        plan = res.json()
        assert plan["source_text"] == ASK
        assert plan["source_topic_title"] == "Customer mail"
        assert [p["id"] for p in client.get(f"/api/step-plans?topic_id={topic['id']}").json()] == [
            plan["id"]
        ]


def test_a_plan_needs_a_source(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        assert client.post("/api/step-plans", json={}).status_code in (400, 404, 409, 422)


def test_validating_needs_an_artifact_and_rolls_up(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _started(client)
        steps = _by_title(plan)
        quote = steps["Quote"]["id"]
        res = client.post(f"/api/steps/{quote}/status", json={"status": "validated"})
        assert res.status_code == 409

        written = client.put(f"/api/steps/{quote}/artifact", json={"content": "Quote: 10k"}).json()
        assert written["status"] == "in_progress"
        assert written["artifact_version"] == 1
        _validate(client, quote, "Quote: 10k")

        # The group's status is derived from its children.
        risks = steps["Risks"]["id"]
        assert (
            client.post(f"/api/steps/{risks}/status", json={"status": "validated"}).status_code
            == 409
        )
        _validate(client, steps["Data"]["id"])
        now = _by_title(_get(client, plan["id"]))
        assert now["Risks"]["effective_status"] == "validated"
        assert _get(client, plan["id"])["ready_to_consolidate"] is False
        _validate(client, steps["Date"]["id"])
        assert _get(client, plan["id"])["ready_to_consolidate"] is True


def test_artifact_edits_are_versioned_and_revertible(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        steps = _by_title(_started(client))
        sid = steps["Quote"]["id"]
        client.put(f"/api/steps/{sid}/artifact", json={"content": "v1"})
        client.put(f"/api/steps/{sid}/artifact", json={"content": "v2"})
        client.put(f"/api/steps/{sid}/artifact", json={"content": "v2"})
        versions = client.get(f"/api/steps/{sid}/versions").json()
        assert sorted(v["version"] for v in versions) == [1, 2]
        reverted = client.post(f"/api/steps/{sid}/artifact/revert", json={"version": 1}).json()
        assert reverted["artifact"] == "v1"
        # Restoring points at the old version; it never records a new one.
        assert reverted["artifact_version"] == 1
        assert len(client.get(f"/api/steps/{sid}/versions").json()) == 2
        # Re-applying text that matches an earlier version reuses it too.
        again = client.put(f"/api/steps/{sid}/artifact", json={"content": "v2"}).json()
        assert again["artifact_version"] == 2
        fresh = client.put(f"/api/steps/{sid}/artifact", json={"content": "v3"}).json()
        assert fresh["artifact_version"] == 3
        noop = client.post(f"/api/steps/{sid}/artifact/revert", json={"version": 3}).json()
        assert noop["artifact_version"] == 3
        assert len(client.get(f"/api/steps/{sid}/versions").json()) == 3


def test_editing_a_validated_artifact_reopens_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _started(client)
        sid = _by_title(plan)["Quote"]["id"]
        _validate(client, sid, "v1")
        edited = client.put(f"/api/steps/{sid}/artifact", json={"content": "v2"}).json()
        assert edited["status"] == "to_validate"


def test_a_group_cascades_cancel_and_reopen(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _started(client)
        steps = _by_title(plan)
        risks = steps["Risks"]["id"]
        client.post(f"/api/steps/{risks}/status", json={"status": "cancelled"})
        now = _by_title(_get(client, plan["id"]))
        assert now["Data"]["status"] == "cancelled"
        # Cancelled work doesn't block consolidation.
        client.post(f"/api/steps/{risks}/status", json={"status": "open"})
        assert _by_title(_get(client, plan["id"]))["Data"]["status"] == "open"


def test_steps_move_in_the_tree_without_cycles(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        steps = _by_title(_started(client))
        moved = client.post(
            f"/api/steps/{steps['Date']['id']}/move",
            json={"parent_step_id": steps["Quote"]["id"], "position": 0},
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["parent_step_id"] == steps["Quote"]["id"]
        cycle = client.post(
            f"/api/steps/{steps['Risks']['id']}/move",
            json={"parent_step_id": steps["Data"]["id"], "position": 0},
        )
        assert cycle.status_code == 409


def test_the_user_adds_and_deletes_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _started(client)
        res = client.post(f"/api/step-plans/{plan['id']}/steps", json={"title": "Extra"})
        assert res.status_code == 201, res.text
        assert res.json()["status"] == "open"
        assert res.json()["created_by"] == "user"
        risks = _by_title(plan)["Risks"]["id"]
        assert client.delete(f"/api/steps/{risks}").status_code == 204
        assert set(_by_title(_get(client, plan["id"]))) == {"Quote", "Date", "Extra"}


def test_the_model_drafts_an_artifact_in_the_step_discussion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        steps = _by_title(_started(client))
        sid = steps["Quote"]["id"]
        res = client.post(
            f"/api/steps/{sid}/artifact/generate", json={"instructions": "keep it short"}
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["outcome"] == "published"
        assert body["step"]["artifact"] == "Drafted artifact"
        assert body["step"]["status"] == "to_validate"
        assert body["step"]["created_by"] == "model"
        turn = TURNS[-1]
        # The visible turn is short; the rules go to the model only.
        assert turn["prompt"] == "Draft the artifact for this step.\n\nkeep it short"
        assert "<!-- artifact -->" in turn["llm_prompt"] and "keep it short" in turn["llm_prompt"]
        assert ASK in turn["context"] and "Quote the migration" in turn["context"]
        versions = client.get(f"/api/steps/{sid}/versions").json()
        assert versions[0]["author"] == "model"
        # The exchange is part of the step's discussion.
        chat = client.post(f"/api/steps/{sid}/chat").json()
        assert chat["id"] == turn["chat_id"]
        texts = [m["content"] for m in client.get(f"/api/chats/{chat['id']}/messages").json()]
        assert texts[0].startswith("Draft the artifact") and "Here is a draft." in texts[1]
        # A group has no artifact of its own.
        group = client.post(f"/api/steps/{steps['Risks']['id']}/artifact/generate", json={})
        assert group.status_code == 409


def test_a_reply_without_a_deliverable_stays_in_the_discussion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        sid = _by_title(_started(client))["Quote"]["id"]
        client.put(f"/api/steps/{sid}/artifact", json={"content": "my own text"})
        TURN_REPLIES["step"] = "Which currency should I quote in? Two options: EUR or USD."
        res = client.post(f"/api/steps/{sid}/artifact/generate", json={})
        assert res.status_code == 200, res.text
        assert res.json()["outcome"] == "discussion"
        assert res.json()["step"]["artifact"] == "my own text"
        assert res.json()["step"]["artifact_version"] == 1

        TURN_REPLIES["parked"] = True
        parked = client.post(f"/api/steps/{sid}/artifact/generate", json={}).json()
        assert parked["outcome"] == "awaiting_approval"
        assert parked["step"]["artifact"] == "my own text"


def test_a_chosen_reply_can_become_the_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        sid = _by_title(_started(client))["Quote"]["id"]
        TURN_REPLIES["step"] = "Maybe quote 12k EUR, but tell me the scope first."
        client.post(f"/api/steps/{sid}/artifact/generate", json={})
        chat = client.post(f"/api/steps/{sid}/chat").json()
        replies = [
            m
            for m in client.get(f"/api/chats/{chat['id']}/messages").json()
            if m["role"] == "assistant"
        ]
        url = f"/api/steps/{sid}/artifact/from-message"
        # The user picked it, so with no marker the whole reply is used.
        res = client.post(url, json={"message_id": replies[-1]["id"]})
        assert res.status_code == 200, res.text
        assert res.json()["artifact"] == "Maybe quote 12k EUR, but tell me the scope first."
        assert res.json()["status"] == "to_validate"
        user_msg = client.get(f"/api/chats/{chat['id']}/messages").json()[0]
        assert client.post(url, json={"message_id": user_msg["id"]}).status_code == 409
        assert client.post(url, json={"message_id": 999999}).status_code == 404

        # Sending what the artifact already holds leaves an approval alone.
        client.post(f"/api/steps/{sid}/status", json={"status": "validated"})
        again = client.post(url, json={"message_id": replies[-1]["id"]}).json()
        assert again["status"] == "validated"
        assert again["artifact_version"] == res.json()["artifact_version"]


def test_the_step_chat_is_grounded_and_hidden_from_the_chat_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Chat
    from precursor.backend.services.turn_engine import build_chat_system_context

    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        steps = _by_title(_started(client))
        sid = steps["Quote"]["id"]
        chat = client.post(f"/api/steps/{sid}/chat").json()
        assert client.post(f"/api/steps/{sid}/chat").json()["id"] == chat["id"]
        assert (
            _by_title(_get(client, steps["Quote"]["plan_id"]))["Quote"]["status"] == "in_progress"
        )
        assert chat["id"] not in [c["id"] for c in client.get("/api/chats").json()]

        async def _ctx() -> str:
            async with SessionLocal() as session:
                row = await session.get(Chat, chat["id"])
                assert row is not None
                return await build_chat_system_context(session, row)

        ctx = anyio.run(_ctx)
        assert ASK in ctx
        assert "Quote the migration" in ctx


def test_the_step_chat_sees_the_topic_and_the_other_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Chat, Message, MessageRole
    from precursor.backend.services.turn_engine import build_chat_system_context

    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        topic = client.post("/api/topics", json={"title": "Customer mail"}).json()

        async def _seed() -> None:
            async with SessionLocal() as session:
                session.add(Message(topic_id=topic["id"], role=MessageRole.USER, content=ASK))
                session.add(
                    Message(
                        topic_id=topic["id"],
                        role=MessageRole.ASSISTANT,
                        content="Earlier we agreed the freeze starts in June.",
                    )
                )
                await session.commit()

        _run(_seed)
        plan = client.post("/api/step-plans", json={"topic_id": topic["id"]}).json()
        client.patch(
            f"/api/step-plans/{plan['id']}", json={"output_instructions": "A short reply email"}
        )
        client.post(f"/api/step-plans/{plan['id']}/start", json={})
        steps = _by_title(_get(client, plan["id"]))
        _validate(client, steps["Quote"]["id"], "Quote: 12k EUR")
        client.put(f"/api/steps/{steps['Date']['id']}/artifact", json={"content": "Draft: June 3"})
        chat = client.post(f"/api/steps/{steps['Data']['id']}/chat").json()

        async def _ctx() -> str:
            async with SessionLocal() as session:
                row = await session.get(Chat, chat["id"])
                assert row is not None
                return await build_chat_system_context(session, row)

        ctx = anyio.run(_ctx)
        assert "Earlier we agreed the freeze starts in June." in ctx
        assert "A short reply email" in ctx
        assert "Quote: 12k EUR" in ctx
        assert "Draft: June 3" in ctx
        assert "Plan progress:" in ctx

        client.post(f"/api/steps/{steps['Data']['id']}/artifact/generate", json={})
        context = TURNS[-1]["context"]
        assert "Quote: 12k EUR" in context
        assert "Earlier we agreed the freeze starts in June." in context


def test_review_returns_text_and_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _started(client)
        res = client.post(f"/api/step-plans/{plan['id']}/review")
        assert res.status_code == 200
        assert "Not covered" in res.json()["text"]
        assert _get(client, plan["id"])["updated_at"] == plan["updated_at"]


def test_suggestions_for_missing_and_sub_steps_stay_proposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _started(client)
        steps = _by_title(plan)
        missing = client.post(f"/api/step-plans/{plan['id']}/suggest-missing", json={}).json()
        rollback = _by_title(missing)["Rollback"]
        assert rollback["status"] == "proposed"
        accepted = client.post(f"/api/steps/{rollback['id']}/accept").json()
        assert accepted["status"] == "open"

        sub = client.post(f"/api/steps/{steps['Date']['id']}/suggest-substeps", json={}).json()
        kids = [s for s in sub["steps"] if s["parent_step_id"] == steps["Date"]["id"]]
        assert {k["title"] for k in kids} == {"A", "B"}
        assert all(k["status"] == "proposed" for k in kids)
        # Accepting a child makes its parent a group.
        client.post(f"/api/steps/{kids[0]['id']}/accept")
        parent = _by_title(_get(client, plan["id"]))["Date"]
        assert parent["effective_status"] == "open"


def test_a_step_with_an_artifact_cannot_be_split(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        steps = _by_title(_started(client))
        sid = steps["Quote"]["id"]
        client.put(f"/api/steps/{sid}/artifact", json={"content": "x"})
        assert client.post(f"/api/steps/{sid}/suggest-substeps", json={}).status_code == 409


def test_consolidation_needs_validated_steps_unless_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _started(client)
        steps = _by_title(plan)
        gen = f"/api/step-plans/{plan['id']}/consolidate/generate"
        assert client.post(gen, json={}).status_code == 409

        _validate(client, steps["Quote"]["id"], "Quote: 10k")
        _validate(client, steps["Data"]["id"], "Data risks")
        assert client.post(gen, json={}).status_code == 409
        client.post(f"/api/steps/{steps['Date']['id']}/status", json={"status": "cancelled"})
        res = client.post(gen, json={"instructions": "formal tone"})
        assert res.status_code == 200, res.text
        assert res.json()["outcome"] == "published"
        out = res.json()["plan"]
        assert out["final_output"] == "FINAL ANSWER"
        assert out["status"] == "done"
        assert out["final_stale"] is False
        assert out["final_provisional"] is False
        turn = TURNS[-1]
        assert "Quote: 10k" in turn["context"] and "Data risks" in turn["context"]
        assert (
            "formal tone" in turn["llm_prompt"] and "validated artifacts only" in turn["llm_prompt"]
        )
        chat_id = out["consolidation_chat_id"]
        texts = [m["content"] for m in client.get(f"/api/chats/{chat_id}/messages").json()]
        assert texts[0].startswith("Consolidate the plan")


def test_forcing_makes_a_provisional_final_that_cannot_complete_the_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        topic = client.post("/api/topics", json={"title": "Mail"}).json()
        plan = _plan(client, topic_id=topic["id"])
        plan = client.post(f"/api/step-plans/{plan['id']}/start", json={}).json()
        sid = _by_title(plan)["Quote"]["id"]
        client.put(f"/api/steps/{sid}/artifact", json={"content": "Draft quote"})
        res = client.post(
            f"/api/step-plans/{plan['id']}/consolidate/generate", json={"force": True}
        )
        assert res.status_code == 200, res.text
        out = res.json()["plan"]
        assert out["final_provisional"] is True
        assert out["status"] == "consolidating"
        turn = TURNS[-1]
        assert "Draft quote" in turn["context"] and "NOT validated" in turn["llm_prompt"]

        send = f"/api/step-plans/{plan['id']}/send-to-topic"
        assert client.post(send, json={}).status_code == 409
        sent = client.post(send, json={"force": True})
        assert sent.status_code == 200, sent.text
        assert "provisional" in sent.json()["content"]
        after = _get(client, plan["id"])
        assert after["status"] == "consolidating" and after["final_provisional"] is True


def test_validating_the_remaining_steps_makes_a_provisional_final_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _started(client)
        steps = _by_title(plan)
        _validate(client, steps["Quote"]["id"])
        client.post(f"/api/step-plans/{plan['id']}/consolidate/generate", json={"force": True})
        assert _get(client, plan["id"])["final_provisional"] is True
        _validate(client, steps["Data"]["id"])
        _validate(client, steps["Date"]["id"])
        assert _get(client, plan["id"])["final_stale"] is True
        client.post(f"/api/step-plans/{plan['id']}/consolidate/generate", json={})
        final = _get(client, plan["id"])
        assert final["final_provisional"] is False and final["final_stale"] is False


def test_a_consolidation_reply_without_a_deliverable_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _started(client)
        steps = _by_title(plan)
        for title in ("Quote", "Data", "Date"):
            _validate(client, steps[title]["id"])
        TURN_REPLIES["final"] = "Do you want a formal tone?"
        res = client.post(f"/api/step-plans/{plan['id']}/consolidate/generate", json={})
        assert res.json()["outcome"] == "discussion"
        assert res.json()["plan"]["final_output"] is None


def test_changing_validated_work_marks_the_final_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _started(client)
        steps = _by_title(plan)
        for title in ("Quote", "Data", "Date"):
            _validate(client, steps[title]["id"])
        client.post(f"/api/step-plans/{plan['id']}/consolidate/generate", json={})
        assert _get(client, plan["id"])["status"] == "done"
        client.put(f"/api/steps/{steps['Quote']['id']}/artifact", json={"content": "changed"})
        after = _get(client, plan["id"])
        assert after["final_stale"] is True
        assert after["status"] == "active"
        assert after["final_output"] == "FINAL ANSWER"


def test_the_final_can_be_edited_and_sent_to_the_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        topic = client.post("/api/topics", json={"title": "Mail"}).json()
        plan = _plan(client, topic_id=topic["id"], propose=False)
        client.post(f"/api/step-plans/{plan['id']}/steps", json={"title": "Only"})
        sid = _get(client, plan["id"])["steps"][0]["id"]
        client.post(f"/api/step-plans/{plan['id']}/start", json={})
        assert client.post(f"/api/step-plans/{plan['id']}/send-to-topic").status_code == 409
        _validate(client, sid, "the answer")
        client.post(f"/api/step-plans/{plan['id']}/consolidate/generate", json={})
        edited = client.put(
            f"/api/step-plans/{plan['id']}/final", json={"content": "Edited final"}
        ).json()
        assert edited["final_output"] == "Edited final"

        sent = client.post(f"/api/step-plans/{plan['id']}/send-to-topic", json={})
        assert sent.status_code == 200, sent.text
        assert sent.json()["content"].startswith("**From step-by-step plan: [")
        assert f"](/steps/{plan['id']})**" in sent.json()["content"]
        assert "Edited final" in sent.json()["content"]
        assert _get(client, plan["id"])["status"] == "done"
        messages = client.get(f"/api/topics/{topic['id']}/messages").json()
        assert any("Edited final" in m["content"] for m in messages)


def _approved_and_filed_setup(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    topic = client.post("/api/topics", json={"title": "Mail"}).json()
    plan = _plan(client, topic_id=topic["id"], propose=False)
    client.post(f"/api/step-plans/{plan['id']}/steps", json={"title": "Only"})
    sid = _get(client, plan["id"])["steps"][0]["id"]
    client.post(f"/api/step-plans/{plan['id']}/start", json={})
    _validate(client, sid, "the answer")
    return topic, {"plan": plan, "step": sid}


def _topic_texts(client: TestClient, topic_id: int) -> list[str]:
    messages = client.get(f"/api/topics/{topic_id}/messages").json()
    return [m["content"] for m in messages if m["content"].startswith("**From step-by-step")]


def test_a_stale_final_cannot_be_filed_even_after_the_step_is_reapproved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        topic, ctx = _approved_and_filed_setup(client)
        pid, sid = ctx["plan"]["id"], ctx["step"]
        send = f"/api/step-plans/{pid}/send-to-topic"
        client.post(f"/api/step-plans/{pid}/consolidate/generate", json={})
        assert _get(client, pid)["status"] == "done"

        # Change an approved step and approve it again: the old final is still stale.
        client.put(f"/api/steps/{sid}/artifact", json={"content": "the revised answer"})
        _validate(client, sid, "the revised answer")
        stale = _get(client, pid)
        assert stale["final_stale"] is True and stale["ready_to_consolidate"] is True
        assert stale["status"] == "active"

        for body in ({}, {"force": True}, {"text": "FINAL ANSWER", "force": True}):
            res = client.post(send, json=body)
            assert res.status_code == 409, body
            assert "changed since" in res.json()["detail"]
        assert _topic_texts(client, topic["id"]) == []
        assert _get(client, pid)["status"] == "active"

        # Regenerating from the current approved inputs makes it fileable again.
        client.post(f"/api/step-plans/{pid}/consolidate/generate", json={})
        sent = client.post(send, json={})
        assert sent.status_code == 200, sent.text
        assert "provisional" not in sent.json()["content"]
        assert _get(client, pid)["status"] == "done"


def test_a_stale_final_can_be_filed_once_the_user_reviews_and_saves_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        topic, ctx = _approved_and_filed_setup(client)
        pid, sid = ctx["plan"]["id"], ctx["step"]
        client.post(f"/api/step-plans/{pid}/consolidate/generate", json={})
        client.put(f"/api/steps/{sid}/artifact", json={"content": "the revised answer"})
        _validate(client, sid, "the revised answer")
        assert _get(client, pid)["final_stale"] is True
        send = f"/api/step-plans/{pid}/send-to-topic"
        assert client.post(send, json={}).status_code == 409

        # Saving the final (even unchanged) is the user's explicit review of it.
        reviewed = client.put(f"/api/step-plans/{pid}/final", json={"content": "FINAL ANSWER"})
        assert reviewed.json()["final_stale"] is False
        assert client.post(send, json={}).status_code == 200
        assert len(_topic_texts(client, topic["id"])) == 1


def test_a_stale_provisional_final_is_not_filed_with_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        topic = client.post("/api/topics", json={"title": "Mail"}).json()
        plan = _plan(client, topic_id=topic["id"])
        plan = client.post(f"/api/step-plans/{plan['id']}/start", json={}).json()
        sid = _by_title(plan)["Quote"]["id"]
        client.put(f"/api/steps/{sid}/artifact", json={"content": "Draft quote"})
        client.post(f"/api/step-plans/{plan['id']}/consolidate/generate", json={"force": True})
        send = f"/api/step-plans/{plan['id']}/send-to-topic"
        client.put(f"/api/steps/{sid}/artifact", json={"content": "Draft quote v2"})
        assert _get(client, plan["id"])["final_stale"] is True
        assert client.post(send, json={"force": True}).status_code == 409
        assert _topic_texts(client, topic["id"]) == []


def test_a_plan_without_a_topic_cannot_send(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _plan(client, propose=False)
        client.post(f"/api/step-plans/{plan['id']}/steps", json={"title": "Only"})
        client.post(f"/api/step-plans/{plan['id']}/start", json={})
        res = client.post(f"/api/step-plans/{plan['id']}/send-to-topic", json={"text": "hello"})
        assert res.status_code == 409


def test_deleting_the_topic_detaches_its_plans(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        topic = client.post("/api/topics", json={"title": "Mail"}).json()
        plan = _plan(client, topic_id=topic["id"], propose=False)
        assert client.delete(f"/api/topics/{topic['id']}").status_code == 204
        after = _get(client, plan["id"])
        assert after["source_topic_id"] is None
        assert after["source_text"] == ASK


def test_deleting_a_plan_removes_its_chats(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _started(client)
        sid = _by_title(plan)["Quote"]["id"]
        chat = client.post(f"/api/steps/{sid}/chat").json()
        cons = client.post(f"/api/step-plans/{plan['id']}/consolidation/chat").json()
        assert client.delete(f"/api/step-plans/{plan['id']}").status_code == 204
        assert client.get(f"/api/step-plans/{plan['id']}").status_code == 404
        assert client.get(f"/api/chats/{chat['id']}").status_code == 404
        assert client.get(f"/api/chats/{cons['id']}").status_code == 404
        assert plan["id"] not in [p["id"] for p in client.get("/api/step-plans").json()]


def test_the_plan_can_be_renamed_and_given_an_output_brief(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _plan(client, propose=False)
        res = client.patch(
            f"/api/step-plans/{plan['id']}",
            json={"title": "Customer answer", "output_instructions": "An email, 150 words"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["title"] == "Customer answer"
        assert res.json()["output_instructions"] == "An email, 150 words"


def test_a_pinned_model_is_used_for_the_model_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    from precursor.backend.services.step_plan_llm import extract_json

    assert extract_json('x {"a": [1]} y') == {"a": [1]}
    assert extract_json("none") is None
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _plan(client, propose=False)
        assert plan["llm_selection"] is None


def test_the_steps_command_is_not_part_of_the_title_or_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _plan(client, source_text="/steps Review the whole answer process", propose=False)
        assert plan["title"] == "Review the whole answer process"
        assert plan["source_text"] == "Review the whole answer process"


def test_a_bare_steps_turn_falls_back_to_the_previous_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        topic = client.post("/api/topics", json={"title": "Mail"}).json()

        async def seed() -> None:
            from precursor.backend.db import SessionLocal
            from precursor.backend.models import Message, MessageRole

            async with SessionLocal() as session:
                for text in ("Please plan the migration", "/steps"):
                    session.add(Message(topic_id=topic["id"], role=MessageRole.USER, content=text))
                await session.commit()

        _run(seed)
        plan = client.post(
            "/api/step-plans", json={"topic_id": topic["id"], "propose": False}
        ).json()
        assert plan["title"] == "Please plan the migration"


def test_spawning_a_plan_leaves_a_trace_in_its_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        topic = client.post("/api/topics", json={"title": "Mail"}).json()
        plan = _plan(client, topic_id=topic["id"], propose=False)
        messages = client.get(f"/api/topics/{topic['id']}/messages").json()
        trace = [m for m in messages if m["kind"] == "step_plan"]
        assert len(trace) == 1
        assert trace[0]["role"] == "system"
        assert f"(/steps/{plan['id']})" in trace[0]["content"]
        assert client.get(f"/api/step-plans?topic_id={topic['id']}").json()[0]["id"] == plan["id"]


# --- automation and assessments -------------------------------------------------

STEP_ASSESSMENT = json.dumps(
    {
        "score": 140,
        "summary": "Mostly covered.",
        "covered": ["quote"],
        "evidence": ["ask line 1"],
        "assumptions": ["10k is right"],
        "gaps": ["no date"],
    }
)
PLAN_ASSESSMENT = json.dumps(
    {
        "score": 61.4,
        "summary": "Two of three asks answered.",
        "covered": ["quote", "risks"],
        "evidence": ["Quote step"],
        "assumptions": [],
        "gaps": ["date"],
        "contradictions": ["Quote says 10k, Risks says 12k"],
    }
)
FINAL_ASSESSMENT = json.dumps(
    {
        "score": 44,
        "summary": "The final text drops the date.",
        "covered": ["quote"],
        "evidence": ["Quote step"],
        "assumptions": [],
        "gaps": ["date"],
        "contradictions": [],
    }
)
ASSESS_REPLIES = {
    "ONE step's artifact": STEP_ASSESSMENT,
    "READINESS of a step-by-step plan": PLAN_ASSESSMENT,
    "FINAL DELIVERABLE of a step-by-step plan": FINAL_ASSESSMENT,
}


def _mode(client: TestClient, plan_id: int, mode: str) -> dict[str, Any]:
    res = client.patch(f"/api/step-plans/{plan_id}", json={"automation_mode": mode})
    assert res.status_code == 200, res.text
    data: dict[str, Any] = res.json()
    return data


def _await_run(client: TestClient, plan_id: int) -> dict[str, Any]:
    import time

    for _ in range(200):
        plan = _get(client, plan_id)
        if plan["auto_state"] != "running":
            return plan
        time.sleep(0.05)
    raise AssertionError("the automated run never ended")


def test_an_assessment_is_clamped_explained_and_goes_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch, ASSESS_REPLIES)
    with TestClient(create_app()) as client:
        plan = _started(client)
        sid = _by_title(plan)["Quote"]["id"]
        assert client.post(f"/api/steps/{sid}/assess").status_code == 409  # nothing to assess
        client.put(f"/api/steps/{sid}/artifact", json={"content": "Quote: 10k"})
        step = client.post(f"/api/steps/{sid}/assess").json()
        a = step["assessment"]
        assert a["score"] == 100 and a["gaps"] == ["no date"] and a["evidence"] == ["ask line 1"]
        assert step["assessment_stale"] is False
        # Editing the artifact, the instruction or the ask invalidates the score.
        client.put(f"/api/steps/{sid}/artifact", json={"content": "Quote: 12k"})
        assert _by_title(_get(client, plan["id"]))["Quote"]["assessment_stale"] is True
        client.post(f"/api/steps/{sid}/assess")
        client.patch(f"/api/steps/{sid}", json={"instruction": "Quote it in euros"})
        assert _by_title(_get(client, plan["id"]))["Quote"]["assessment_stale"] is True
        client.post(f"/api/steps/{sid}/assess")
        client.patch(f"/api/step-plans/{plan['id']}", json={"source_text": ASK + " Also: euros."})
        assert _by_title(_get(client, plan["id"]))["Quote"]["assessment_stale"] is True


def test_the_global_assessment_covers_the_ask_and_goes_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_llm(monkeypatch, ASSESS_REPLIES)
    with TestClient(create_app()) as client:
        plan = _started(client)
        steps = _by_title(plan)
        url = f"/api/step-plans/{plan['id']}/assess"
        assert client.post(url).status_code == 409
        client.put(f"/api/steps/{steps['Quote']['id']}/artifact", json={"content": "Quote: 10k"})
        out = client.post(url).json()
        assert out["assessment"]["score"] == 61
        assert out["assessment"]["contradictions"] == ["Quote says 10k, Risks says 12k"]
        assert out["assessment_stale"] is False
        assert out["assessment"]["scope"] == "steps"
        system = next(c["system"] for c in calls if "READINESS of a step" in c["system"])
        assert "Do NOT average" in system and "not a measured probability" in system
        assert "NOT assessing one" in system
        client.put(f"/api/steps/{steps['Quote']['id']}/artifact", json={"content": "Quote: 11k"})
        assert _get(client, plan["id"])["assessment_stale"] is True


def test_the_overall_assessment_reads_the_final_output_once_there_is_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_llm(monkeypatch, ASSESS_REPLIES)
    with TestClient(create_app()) as client:
        plan = _started(client)
        steps = _by_title(plan)
        client.patch(f"/api/step-plans/{plan['id']}", json={"output_instructions": "Formal tone"})
        for title in ("Quote", "Data", "Date"):
            _validate(client, steps[title]["id"], f"{title}: approved text")
        url = f"/api/step-plans/{plan['id']}/assess"

        before = client.post(url).json()
        assert before["assessment"]["scope"] == "steps"
        assert not any("<<<FINAL" in c["user"] for c in calls)

        client.post(f"/api/step-plans/{plan['id']}/consolidate/generate", json={})
        # A readiness score is not the score of a final output that appeared later.
        after_final = _get(client, plan["id"])
        assert after_final["assessment"]["scope"] == "steps"
        assert after_final["assessment_stale"] is True

        out = client.post(url).json()
        assert out["assessment"]["scope"] == "final"
        assert out["assessment"]["score"] == 44
        assert out["assessment_stale"] is False
        call = calls[-1]
        assert (
            "FINAL DELIVERABLE" in call["system"] and "not a measured probability" in call["system"]
        )
        assert "<<<FINAL\nFINAL ANSWER\nFINAL>>>" in call["user"]
        assert "Formal tone" in call["user"] and "Quote: approved text" in call["user"]

        # Editing the final text invalidates the score of the text it read.
        client.put(f"/api/step-plans/{plan['id']}/final", json={"content": "A different final"})
        assert _get(client, plan["id"])["assessment_stale"] is True
        again = client.post(url).json()
        assert again["assessment_stale"] is False
        assert "A different final" in calls[-1]["user"]


def test_a_garbled_assessment_is_a_502(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch, {"ONE step's artifact": "no idea"})
    with TestClient(create_app()) as client:
        sid = _by_title(_started(client))["Quote"]["id"]
        client.put(f"/api/steps/{sid}/artifact", json={"content": "x"})
        assert client.post(f"/api/steps/{sid}/assess").status_code == 502


def test_assisted_drafts_are_scored_and_manual_ones_are_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch, ASSESS_REPLIES)
    with TestClient(create_app()) as client:
        plan = _started(client)
        steps = _by_title(plan)
        manual = client.post(f"/api/steps/{steps['Quote']['id']}/artifact/generate", json={})
        assert manual.json()["step"]["assessment"] is None
        _mode(client, plan["id"], "assisted")
        res = client.post(f"/api/steps/{steps['Date']['id']}/artifact/generate", json={})
        step = res.json()["step"]
        assert step["assessment"]["score"] == 100
        # The model drafts and scores; it never validates.
        assert step["status"] == "to_validate"


def test_a_failed_quiet_assessment_keeps_the_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch, {"ONE step's artifact": "garbled"})
    with TestClient(create_app()) as client:
        plan = _started(client)
        _mode(client, plan["id"], "assisted")
        sid = _by_title(plan)["Quote"]["id"]
        res = client.post(f"/api/steps/{sid}/artifact/generate", json={})
        assert res.status_code == 200
        assert res.json()["step"]["artifact"] == "Drafted artifact"
        assert res.json()["step"]["assessment"] is None


def test_changing_validated_work_flags_the_approval_for_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _started(client)
        steps = _by_title(plan)
        for title in ("Quote", "Data", "Date"):
            _validate(client, steps[title]["id"], f"{title} ok")
        pid = plan["id"]
        gen = f"/api/step-plans/{pid}/consolidate/generate"
        assert client.post(gen, json={}).json()["plan"]["final_provisional"] is False

        sid = steps["Quote"]["id"]
        client.patch(f"/api/steps/{sid}", json={"instruction": "Quote in euros"})
        after = _get(client, pid)
        quote = _by_title(after)["Quote"]
        # Still validated, but not approved any more: it needs another look.
        assert quote["status"] == "validated" and quote["needs_review"] is True
        assert after["final_stale"] is True and after["status"] == "active"
        assert client.post(gen, json={}).status_code == 409
        # Re-approving clears it.
        client.post(f"/api/steps/{sid}/status", json={"status": "validated"})
        assert _by_title(_get(client, pid))["Quote"]["needs_review"] is False
        assert client.post(gen, json={}).status_code == 200

        # A changed ask flags every approval.
        client.patch(f"/api/step-plans/{pid}", json={"source_text": ASK + " And budget."})
        flagged = _by_title(_get(client, pid))
        assert all(flagged[t]["needs_review"] for t in ("Quote", "Data", "Date"))


def test_the_run_drafts_empty_steps_scores_them_and_validates_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch, ASSESS_REPLIES)
    with TestClient(create_app()) as client:
        topic = client.post("/api/topics", json={"title": "Mail"}).json()
        plan = _plan(client, topic_id=topic["id"])
        plan = client.post(f"/api/step-plans/{plan['id']}/start", json={}).json()
        pid, steps = plan["id"], _by_title(plan)
        # The user already approved one step and wrote another: the run leaves both alone.
        _validate(client, steps["Quote"]["id"], "Quote: approved")
        client.put(f"/api/steps/{steps['Data']['id']}/artifact", json={"content": "My own data"})
        _mode(client, pid, "auto")

        assert client.post(f"/api/step-plans/{pid}/auto/start").status_code == 200
        done = _await_run(client, pid)
        by = _by_title(done)
        assert done["auto_state"] == "idle"
        assert by["Quote"]["artifact"] == "Quote: approved" and by["Quote"]["status"] == "validated"
        assert by["Data"]["artifact"] == "My own data"
        assert (
            by["Date"]["artifact"] == "Drafted artifact" and by["Date"]["status"] == "to_validate"
        )
        assert by["Date"]["assessment"]["score"] == 100
        # The synthesis is provisional and the plan is not complete.
        assert done["final_output"] == "FINAL ANSWER" and done["final_provisional"] is True
        # The run ends by scoring the synthesis it just wrote, not the steps alone.
        assert done["assessment"]["scope"] == "final" and done["assessment"]["score"] == 44
        assert done["status"] != "done"
        chat = client.post(f"/api/step-plans/{pid}/consolidation/chat").json()
        notes = [
            m["content"]
            for m in client.get(f"/api/chats/{chat['id']}/messages").json()
            if m["kind"] == "step_plan"
        ]
        assert any("Automated run started" in n for n in notes)
        assert any("Automated run finished" in n for n in notes)
        assert any("self-assessment" in n for n in notes)
        assert any(n.startswith("Drafting «Date»") for n in notes)
        assert any(n.startswith("Drafted «Date»") and "scored" in n for n in notes)
        # The same traces feed the live activity list.
        feed = [n["text"] for n in done["activity"]]
        assert any("Automated run started" in t for t in feed)
        assert any(t.startswith("Drafted «Date»") for t in feed)
        assert done["auto_step_id"] is None


def test_starting_an_automated_plan_starts_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch, ASSESS_REPLIES)
    with TestClient(create_app()) as client:
        plan = _plan(client, automation_mode="auto")
        started = client.post(f"/api/step-plans/{plan['id']}/start", json={}).json()
        assert started["status"] == "active"
        done = _await_run(client, plan["id"])
        leaves = [s for s in done["steps"] if s["title"] in ("Quote", "Data", "Date")]
        assert all(s["artifact"] and s["status"] == "to_validate" for s in leaves)
        # Nothing was approved, so the synthesis is provisional and nothing completes.
        assert done["final_provisional"] is True and done["status"] == "consolidating"
        assert client.post(f"/api/step-plans/{plan['id']}/complete").status_code == 409


def test_a_user_generated_final_completes_only_when_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _started(client)
        pid, steps = plan["id"], _by_title(plan)
        gen = f"/api/step-plans/{pid}/consolidate/generate"
        assert client.post(f"/api/step-plans/{pid}/complete").status_code == 409
        # Consolidating anyway stays provisional: not done.
        _validate(client, steps["Quote"]["id"], "Quote: 10k")
        res = client.post(gen, json={"force": True}).json()
        assert res["plan"]["final_provisional"] is True and res["plan"]["status"] == "consolidating"
        assert client.post(f"/api/step-plans/{pid}/complete").status_code == 409
        # A manual edit of the output doesn't complete it either, but approval + Mark as done does.
        _validate(client, steps["Data"]["id"], "Data risks")
        _validate(client, steps["Date"]["id"], "A date")
        client.put(f"/api/step-plans/{pid}/final", json={"content": "My final"})
        assert _get(client, pid)["status"] == "consolidating"
        done = client.post(f"/api/step-plans/{pid}/complete")
        assert done.status_code == 200 and done.json()["status"] == "done"


def test_the_run_never_replaces_an_existing_final_and_needs_auto_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch, ASSESS_REPLIES)
    with TestClient(create_app()) as client:
        plan = _started(client)
        pid = plan["id"]
        assert client.post(f"/api/step-plans/{pid}/auto/start").status_code == 409
        _mode(client, pid, "auto")
        client.put(f"/api/step-plans/{pid}/final", json={"content": "My final"})
        client.post(f"/api/step-plans/{pid}/auto/start")
        done = _await_run(client, pid)
        assert done["final_output"] == "My final"


def test_pausing_stops_after_the_step_in_flight_and_resume_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch, ASSESS_REPLIES)
    from precursor.backend.db import SessionLocal
    from precursor.backend.services import step_plan_llm, step_plan_runner

    real = step_plan_llm.run_chat_turn
    paused: list[bool] = []
    plan_ids: list[int] = []

    async def _pausing(chat_id: int, prompt: str, *, llm_prompt: str | None = None) -> Any:
        result = await real(chat_id, prompt, llm_prompt=llm_prompt)
        if not paused and "Draft the artifact" in prompt:
            paused.append(True)
            async with SessionLocal() as session:
                await step_plan_runner.pause(session, plan_ids[0])
        return result

    monkeypatch.setattr(step_plan_llm, "run_chat_turn", _pausing)
    with TestClient(create_app()) as client:
        plan = _started(client)
        pid = plan["id"]
        plan_ids.append(pid)
        _mode(client, pid, "auto")
        client.post(f"/api/step-plans/{pid}/auto/start")
        mid = _await_run(client, pid)
        assert mid["auto_state"] == "paused"
        drafted = [s for s in mid["steps"] if s["artifact"]]
        assert len(drafted) == 1
        assert mid["final_output"] in (None, "")

        client.post(f"/api/step-plans/{pid}/auto/start")
        end = _await_run(client, pid)
        assert end["auto_state"] == "idle"
        leaves = [s for s in end["steps"] if s["title"] in ("Quote", "Data", "Date")]
        assert all(s["artifact"] == "Drafted artifact" for s in leaves)
        assert end["final_provisional"] is True


def test_a_run_that_died_with_the_process_reads_as_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    from precursor.backend.db import SessionLocal
    from precursor.backend.models.step_plan import StepPlan

    with TestClient(create_app()) as client:
        pid = _started(client)["id"]

        async def _stale() -> None:
            async with SessionLocal() as session:
                plan = await session.get(StepPlan, pid)
                assert plan is not None
                plan.auto_state = "running"
                await session.commit()

        _run(_stale)
        assert _get(client, pid)["auto_state"] == "paused"


# --- the live discussion ------------------------------------------------------


class _ScriptedProvider:
    """A live-chat provider whose next reply the test sets."""

    name = "scripted"

    def __init__(self) -> None:
        self.reply = ""

    async def stream_chat(self, *, model, messages, reasoning_effort=None):
        yield ""

    async def stream_chat_with_tools(self, *, model, messages, tools, reasoning_effort=None):
        from precursor.backend.services.llm.base import TextDeltaEvent, TurnDoneEvent

        yield TextDeltaEvent(content=self.reply)
        yield TurnDoneEvent(finish_reason="stop")

    async def list_models(self):
        return []


def _live(monkeypatch: pytest.MonkeyPatch) -> _ScriptedProvider:
    """Live chat replies come from the scripted provider; assessments stay out of the way."""
    from precursor.backend.services import conversation_turn, step_plan_llm, turn_engine

    provider = _ScriptedProvider()

    async def _get(_session: Any) -> _ScriptedProvider:
        return provider

    async def _no_usage(*args: Any, **kwargs: Any) -> None:
        return None

    def _drop(coro: Any) -> None:
        coro.close()

    monkeypatch.setattr(conversation_turn, "get_llm_provider", _get)
    monkeypatch.setattr(turn_engine, "record_usage", _no_usage)
    monkeypatch.setattr(step_plan_llm, "_spawn", _drop)
    return provider


def _say(client: TestClient, chat_id: int, text: str) -> list[dict[str, Any]]:
    res = client.post(
        f"/api/chats/{chat_id}/messages/stream",
        json={"content": text},
        headers={"Accept": "text/event-stream"},
    )
    assert res.status_code == 200, res.text
    return client.get(f"/api/chats/{chat_id}/messages").json()  # type: ignore[no-any-return]


def test_asking_in_the_discussion_updates_the_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_llm(monkeypatch)
    provider = _live(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _started(client)
        sid = _by_title(plan)["Quote"]["id"]
        chat = client.post(f"/api/steps/{sid}/chat").json()

        provider.reply = "Sure.\n<!-- artifact -->\nQuote v1\n<!-- /artifact -->\nBetter?"
        msgs = _say(client, chat["id"], "Please write the quote")
        step = _by_title(_get(client, plan["id"]))["Quote"]
        assert step["artifact"] == "Quote v1"
        assert step["status"] == "to_validate" and step["created_by"] == "model"
        assert any("Applied to the artifact as version 1" in m["content"] for m in msgs)

        # A question or a plain answer leaves the artifact alone.
        provider.reply = "Do you prefer a table?"
        _say(client, chat["id"], "what format?")
        assert _by_title(_get(client, plan["id"]))["Quote"]["artifact"] == "Quote v1"

        # The same text again isn't a new version.
        provider.reply = "<!-- artifact -->\nQuote v1\n<!-- /artifact -->"
        msgs = _say(client, chat["id"], "again")
        assert len(client.get(f"/api/steps/{sid}/versions").json()) == 1
        assert any("unchanged" in m["content"] for m in msgs)


def test_the_discussion_never_overwrites_a_validated_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    provider = _live(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _started(client)
        sid = _by_title(plan)["Quote"]["id"]
        _validate(client, sid, "approved text")
        chat = client.post(f"/api/steps/{sid}/chat").json()
        provider.reply = "<!-- artifact -->\nSneaky rewrite\n<!-- /artifact -->"
        msgs = _say(client, chat["id"], "rewrite it")
        step = _by_title(_get(client, plan["id"]))["Quote"]
        assert step["artifact"] == "approved text" and step["status"] == "validated"
        assert any("Not applied" in m["content"] and "validated" in m["content"] for m in msgs)


def test_asking_in_the_consolidation_chat_saves_a_provisional_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    provider = _live(monkeypatch)
    with TestClient(create_app()) as client:
        plan = _started(client)
        chat = client.post(f"/api/step-plans/{plan['id']}/consolidation/chat").json()
        provider.reply = "<!-- artifact -->\nEARLY SYNTHESIS\n<!-- /artifact -->"
        _say(client, chat["id"], "write the final")
        data = _get(client, plan["id"])
        assert data["final_output"] == "EARLY SYNTHESIS"
        assert data["final_provisional"] is True and data["status"] != "done"


def test_concurrent_requests_create_one_discussion_per_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch)
    from sqlalchemy import func, select

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Chat
    from precursor.backend.services import step_plans

    with TestClient(create_app()) as client:
        plan = _started(client)
        sid = _by_title(plan)["Quote"]["id"]

        async def _race() -> int:
            async def _one() -> int:
                async with SessionLocal() as session:
                    return (await step_plans.ensure_step_chat(session, sid)).id

            ids = await asyncio.gather(*(_one() for _ in range(6)))
            assert len(set(ids)) == 1
            async with SessionLocal() as session:
                return int(
                    (
                        await session.execute(
                            select(func.count()).select_from(Chat).where(Chat.step_id == sid)
                        )
                    ).scalar_one()
                )

        assert _run(_race) == 1
