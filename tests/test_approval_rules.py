"""Approval rules: matching, precedence, the HTTP API and the gate they feed.

Rules are standing allow / ask answers in front of a conversation's approval
policy; the matching tests are pure, the rest drive the real app with a
scripted provider (reusing the fixtures of ``test_conversation_approvals``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services.approval_rules import (
    ArgClause,
    CompiledRule,
    first_match,
    glob_escape,
    parse_clauses,
    path_is_under,
)
from precursor.backend.services.approvals import ApprovalGate
from tests.test_conversation_approvals import (
    _WRITE,
    _answer,
    _Call,
    _conversation,
    _decide,
    _install,
    _resume,
    _ScriptedProvider,
    _send,
    _tool_round,
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c
        # Global rules outlive a test (the worker's tests share one database)
        # and would gate every later turn.
        for rule in c.get("/api/approval-rules").json():
            c.delete(f"/api/approval-rules/{rule['id']}")


def _rule(
    rule_id: int, effect: str, *clauses: ArgClause, rank: int = 1, tool: str = "publish"
) -> CompiledRule:
    return CompiledRule(
        id=rule_id, effect=effect, server="acme", tool=tool, clauses=clauses, rank=rank
    )


# -- matching ---------------------------------------------------------------


def test_under_matches_the_folder_itself_and_its_children_only() -> None:
    assert path_is_under("docs/a.md", "docs")
    assert path_is_under("docs", "docs")
    assert path_is_under("/docs/./sub/../a.md", "/docs")
    assert not path_is_under("docs-private/a.md", "docs")
    assert not path_is_under("docs/../secrets/a.md", "docs")
    assert not path_is_under("../docs/a.md", "docs")
    assert path_is_under("C:\\w\\a.md".replace("\\", "/"), "C:/w")


def test_clause_operators_and_missing_arguments() -> None:
    args = {"path": "docs/a.md", "meta": {"mode": "w"}, "n": 3, "flag": True, "items": [1]}
    assert ArgClause("path", "equals", "docs/a.md").matches(args)
    assert ArgClause("path", "glob", "docs/*.md").matches(args)
    assert ArgClause("path", "under", "docs").matches(args)
    assert ArgClause("meta.mode", "equals", "w").matches(args)
    assert ArgClause("n", "equals", "3").matches(args)
    assert not ArgClause("n", "under", "3").matches(args)
    assert not ArgClause("flag", "equals", "True").matches(args)
    assert not ArgClause("items", "equals", "[1]").matches(args)
    assert not ArgClause("absent", "glob", "*").matches(args)
    assert not ArgClause("path.deeper", "glob", "*").matches(args)


def test_a_malformed_stored_clause_never_matches_instead_of_widening_the_rule() -> None:
    assert parse_clauses("not json") == ()
    clauses = parse_clauses(json.dumps([{"arg": "path", "op": "bogus", "value": "x"}]))
    rule = CompiledRule(id=1, effect="allow", server="*", tool="*", clauses=clauses)
    assert first_match([rule], "acme", "publish", "{}") is None


def test_tool_globs_and_escaping() -> None:
    star = _rule(1, "allow", tool="pub*")
    assert first_match([star], "acme", "publish", "{}") is star
    assert first_match([star], "acme", "lookup", "{}") is None
    assert first_match([star], "other", "publish", "{}") is None
    literal = _rule(2, "allow", tool=glob_escape("we[ir]d*"))
    assert first_match([literal], "acme", "we[ir]d*", "{}") is literal
    assert first_match([literal], "acme", "weird", "{}") is None


def test_conversation_rules_beat_global_and_ask_beats_allow_at_the_same_level() -> None:
    global_ask = _rule(1, "ask", rank=1)
    own_allow = _rule(2, "allow", rank=0)
    assert first_match([global_ask, own_allow], "acme", "publish", "{}") is own_allow

    allow = _rule(3, "allow")
    ask = _rule(4, "ask", ArgClause("path", "under", "prod"))
    both = [allow, ask]
    assert first_match(both, "acme", "publish", json.dumps({"path": "prod/x"})) is ask
    assert first_match(both, "acme", "publish", json.dumps({"path": "dev/x"})) is allow


def test_rules_decide_before_the_policy() -> None:
    call = ("acme__publish", "acme", "publish", _WRITE)
    allow, ask = _rule(1, "allow"), _rule(2, "ask")
    assert ApprovalGate("manual", (allow,)).check(*call).reason is None
    assert ApprovalGate("manual", (allow,)).check(*call).rule_id == 1
    verdict = ApprovalGate("autonomous", (ask,)).check(*call)
    assert verdict.reason is not None and verdict.rule_id == 2
    assert ApprovalGate("balanced", ()).check(*call).rule_id is None


# -- the HTTP API ------------------------------------------------------------


def _create(client: TestClient, **body: Any) -> dict[str, Any]:
    payload = {"effect": "allow", "server": "acme", "tool": "publish", **body}
    r = client.post("/api/approval-rules", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_rule_crud_round_trip(client: TestClient) -> None:
    rule = _create(
        client,
        arg_match=[{"arg": "path", "op": "under", "value": "docs/../docs/"}],
        note="docs only",
    )
    assert rule["scope"] == "global" and rule["hit_count"] == 0
    assert rule["arg_match"] == [{"arg": "path", "op": "under", "value": "docs"}]

    assert (
        _create(
            client, arg_match=[{"arg": "path", "op": "under", "value": "docs"}], note="docs only"
        )["id"]
        == rule["id"]
    )

    r = client.patch(f"/api/approval-rules/{rule['id']}", json={"effect": "ask", "enabled": False})
    assert r.status_code == 200 and r.json()["effect"] == "ask" and not r.json()["enabled"]
    r = client.patch(f"/api/approval-rules/{rule['id']}", json={"note": None})
    assert r.json()["note"] is None

    listed = client.get("/api/approval-rules", params={"scope": "global"}).json()
    assert rule["id"] in [x["id"] for x in listed]

    assert client.delete(f"/api/approval-rules/{rule['id']}").status_code == 204
    assert client.patch(f"/api/approval-rules/{rule['id']}", json={}).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"scope": "chat"},
        {"scope": "global", "chat_id": 1},
        {"scope": "topic", "chat_id": 1},
        {"effect": "deny"},
        {"tool": ""},
        {"arg_match": [{"arg": "path", "op": "under", "value": "../etc"}]},
        {"arg_match": [{"arg": "path", "op": "under", "value": "."}]},
        {"arg_match": [{"arg": "bad name", "op": "equals", "value": "x"}]},
        {"arg_match": [{"arg": "p", "op": "regex", "value": "x"}]},
        {"arg_match": [{"arg": "p", "op": "equals", "value": "x"}] * 6},
    ],
)
def test_invalid_rules_are_refused(client: TestClient, body: dict[str, Any]) -> None:
    r = client.post("/api/approval-rules", json={"effect": "allow", **body})
    assert r.status_code == 422, r.text


def test_conversation_rules_need_an_existing_owner_and_go_with_it(client: TestClient) -> None:
    r = client.post(
        "/api/approval-rules", json={"effect": "allow", "scope": "chat", "chat_id": 999999}
    )
    assert r.status_code == 404
    cid = _conversation(client, "chat", None)
    rule = _create(client, scope="chat", chat_id=cid)
    assert [x["id"] for x in client.get("/api/approval-rules", params={"chat_id": cid}).json()] == [
        rule["id"]
    ]
    assert client.delete(f"/api/chats/{cid}").status_code == 204
    assert client.get("/api/approval-rules", params={"chat_id": cid}).json() == []


# -- the gate in a real turn ---------------------------------------------------


def test_an_allow_rule_lets_a_matching_call_through_a_manual_conversation(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    provider = _ScriptedProvider(_tool_round(_Call("w1", "acme__publish")), _answer("done"))
    executed = _install(monkeypatch, provider)
    cid = _conversation(client, "chat", "manual")
    rule = _create(client, scope="chat", chat_id=cid)

    events = _send(client, "chat", cid)

    assert "approval_required" not in [n for n, _ in events]
    assert [e[1] for e in executed] == ["publish"]
    hit = client.get("/api/approval-rules", params={"chat_id": cid}).json()[0]
    assert hit["id"] == rule["id"] and hit["hit_count"] == 1 and hit["last_used_at"]


def test_an_ask_rule_parks_a_call_the_autonomous_policy_would_run(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    provider = _ScriptedProvider(_tool_round(_Call("w1", "acme__publish")), _answer("done"))
    executed = _install(monkeypatch, provider)
    cid = _conversation(client, "topic", "autonomous")
    _create(client, effect="ask", note="prod is shared")

    events = _send(client, "topic", cid)

    cards = events[-1][1]["approvals"]
    assert events[-1][0] == "approval_required" and executed == []
    assert "prod is shared" in cards[0]["reason"]


def test_path_conditions_split_calls_under_a_balanced_policy(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    inside = json.dumps({"path": "docs/guide.md"})
    outside = json.dumps({"path": "docs/../secrets.md"})
    provider = _ScriptedProvider(
        _tool_round(_Call("w1", "acme__publish", inside), _Call("w2", "acme__publish", outside)),
        _answer("done"),
    )
    executed = _install(monkeypatch, provider)
    cid = _conversation(client, "chat", "balanced")
    _create(
        client,
        scope="chat",
        chat_id=cid,
        arg_match=[{"arg": "path", "op": "under", "value": "docs"}],
    )

    cards = _send(client, "chat", cid)[-1][1]["approvals"]

    assert [json.loads(c["arguments"])["path"] for c in cards] == ["docs/../secrets.md"]
    assert [e[2] for e in executed] == [{"path": "docs/guide.md"}]


# -- "always allow" saves a rule -----------------------------------------------


def test_always_allow_saves_a_scoped_rule_that_covers_later_conversations(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    args = json.dumps({"path": "docs/a.md"})
    provider = _ScriptedProvider(
        _tool_round(_Call("w1", "acme__publish", args)),
        _answer("one"),
        _tool_round(_Call("w2", "acme__publish", json.dumps({"path": "docs/b.md"}))),
        _answer("two"),
    )
    executed = _install(monkeypatch, provider)
    first = _conversation(client, "chat", "balanced")
    second = _conversation(client, "chat", "balanced")

    cards = _send(client, "chat", first)[-1][1]["approvals"]
    _decide(
        client,
        "chat",
        first,
        cards[0]["id"],
        approve=True,
        scope="global",
        arg_match=[{"arg": "path", "op": "under", "value": "docs"}],
    )
    _resume(client, "chat", first)

    rules = client.get("/api/approval-rules", params={"scope": "global"}).json()
    saved = next(r for r in rules if r["created_from_approval_id"] == cards[0]["id"])
    assert (saved["effect"], saved["server"], saved["tool"]) == ("allow", "acme", "publish")

    events = _send(client, "chat", second)
    assert "approval_required" not in [n for n, _ in events]
    assert [e[2]["path"] for e in executed] == ["docs/a.md", "docs/b.md"]


def test_always_allow_refuses_conditions_the_approved_call_does_not_meet(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    provider = _ScriptedProvider(
        _tool_round(_Call("w1", "acme__publish", json.dumps({"path": "other/a.md"}))),
        _answer("done"),
    )
    _install(monkeypatch, provider)
    cid = _conversation(client, "chat", "manual")
    card = _send(client, "chat", cid)[-1][1]["approvals"][0]

    r = client.post(
        f"/api/chats/{cid}/approvals/{card['id']}",
        json={
            "approve": True,
            "scope": "conversation",
            "arg_match": [{"arg": "path", "op": "under", "value": "docs"}],
        },
    )

    assert r.status_code == 422
    assert client.get("/api/approval-rules", params={"chat_id": cid}).json() == []
