"""Approval policies for chat and topic turns.

A gated tool call parks the turn: the pending call is persisted, the stream
ends with ``approval_required``, and ``POST …/approvals/resume`` continues it
once every card has an answer. These tests drive that loop end to end through
the HTTP API with a scripted provider and a fake MCP manager.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.types import CallToolResult, TextContent

from precursor.backend.main import create_app
from precursor.backend.services import app_settings
from precursor.backend.services.approval_rules import CompiledRule
from precursor.backend.services.approvals import (
    ApprovalGate,
    is_read_only,
)
from precursor.backend.services.llm.base import (
    ChatMessage,
    ProviderEvent,
    TextDeltaEvent,
    ToolCallsEvent,
    ToolDef,
    TurnDoneEvent,
    UsageEvent,
)
from precursor.backend.services.llm.registry import PROVIDERS, ProviderSpec
from precursor.backend.services.mcp.client import (
    ActiveTools,
    MCPToolDef,
    get_mcp_client_manager,
)

_FAKE_PROVIDER = "test-approvals-fake"

_READ = MCPToolDef(
    server="acme",
    name="lookup",
    description="Look something up",
    input_schema={"type": "object", "properties": {}},
    read_only=True,
)
_WRITE = MCPToolDef(
    server="acme",
    name="publish",
    description="Publish something",
    input_schema={"type": "object", "properties": {}},
)


class _Call:
    def __init__(self, call_id: str, name: str, arguments: str = "{}") -> None:
        self.id = call_id
        self.name = name
        self.arguments = arguments


class _ScriptedProvider:
    name = "scripted"

    def __init__(self, *rounds: list[ProviderEvent]) -> None:
        self.rounds = list(rounds)
        self.calls: list[dict[str, Any]] = []

    async def stream_chat_with_tools(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDef],
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        _ = model, reasoning_effort
        self.calls.append({"messages": list(messages), "tools": [t.name for t in tools]})
        for event in self.rounds[min(len(self.calls), len(self.rounds)) - 1]:
            yield event


class _Bundle(ActiveTools):
    handler: Any = None

    async def call_tool(self, server: str, raw_name: str, args: dict[str, Any]) -> Any:
        return await self.handler(server, raw_name, args)


def _tool_round(*calls: _Call) -> list[ProviderEvent]:
    return [
        ToolCallsEvent(calls=list(calls)),
        UsageEvent(prompt_tokens=5, completion_tokens=3, total_tokens=8),
        TurnDoneEvent(finish_reason="tool_calls"),
    ]


def _answer(text: str) -> list[ProviderEvent]:
    return [
        TextDeltaEvent(content=text),
        UsageEvent(prompt_tokens=7, completion_tokens=2, total_tokens=9),
        TurnDoneEvent(finish_reason="stop"),
    ]


def _install(
    monkeypatch: pytest.MonkeyPatch,
    provider: _ScriptedProvider,
    *,
    extra_tools: Sequence[MCPToolDef] = (),
) -> list[tuple[str, str, dict[str, Any]]]:
    """Wire the provider and a fake MCP manager; returns the executed tool calls."""
    monkeypatch.setitem(
        PROVIDERS,
        _FAKE_PROVIDER,
        ProviderSpec(id=_FAKE_PROVIDER, label="Fake", build=lambda _cfg, _tok: provider),
    )

    async def _resolve(_session: object) -> str:
        return _FAKE_PROVIDER

    monkeypatch.setattr(app_settings, "resolve_llm_provider", _resolve)

    executed: list[tuple[str, str, dict[str, Any]]] = []

    async def handler(server: str, raw_name: str, args: dict[str, Any]) -> Any:
        executed.append((server, raw_name, args))
        return CallToolResult(content=[TextContent(type="text", text=f"{raw_name} done")])

    manager = get_mcp_client_manager()

    @asynccontextmanager
    async def acquired(
        names: list[str], *, github_token: str = "", advertise_cached: bool = False
    ) -> AsyncIterator[ActiveTools]:
        _ = names, github_token, advertise_cached
        bundle = _Bundle(manager=manager)
        bundle.handler = handler
        for tool in (_READ, _WRITE, *extra_tools):
            bundle.tools.append(tool)
            bundle.tool_to_server[tool.qualified_name] = (tool.server, tool.name)
        yield bundle

    monkeypatch.setattr(manager, "acquired", acquired)
    return executed


def _events(body: str) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        name = None
        data: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:") :].strip())
        if name:
            out.append((name, json.loads("\n".join(data)) if data else {}))
    return out


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def _conversation(client: TestClient, kind: str, policy: str | None) -> int:
    body: dict[str, Any] = {"title": f"Approvals {kind}"}
    if policy is not None:
        body["approval_policy"] = policy
    r = client.post("/api/topics" if kind == "topic" else "/api/chats", json=body)
    assert r.status_code in (200, 201), r.text
    return int(r.json()["id"])


def _base(kind: str, cid: int) -> str:
    return f"/api/{'topics' if kind == 'topic' else 'chats'}/{cid}"


def _send(client: TestClient, kind: str, cid: int, content: str = "go") -> list[tuple[str, Any]]:
    r = client.post(f"{_base(kind, cid)}/messages/stream", json={"content": content})
    assert r.status_code == 200, r.text
    return _events(r.text)


def _resume(client: TestClient, kind: str, cid: int) -> list[tuple[str, Any]]:
    r = client.post(f"{_base(kind, cid)}/approvals/resume")
    assert r.status_code == 200, r.text
    return _events(r.text)


def _decide(client: TestClient, kind: str, cid: int, approval_id: int, **body: Any) -> Any:
    r = client.post(f"{_base(kind, cid)}/approvals/{approval_id}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _delegated_discussion(
    client: TestClient, source_kind: str, source_id: int, policy: str | None, surface: str
) -> tuple[int, int]:
    plan_response = client.post(
        "/api/step-plans",
        json={
            f"{source_kind}_id": source_id,
            "source_text": "Prepare a result.",
            "propose": False,
            "delegation_policy": policy,
        },
    )
    assert plan_response.status_code == 201, plan_response.text
    plan_id = plan_response.json()["id"]
    if surface == "final":
        chat_response = client.post(f"/api/step-plans/{plan_id}/consolidation/chat")
    else:
        step_response = client.post(
            f"/api/step-plans/{plan_id}/steps", json={"title": "Prepare", "instruction": "Draft"}
        )
        assert step_response.status_code == 201, step_response.text
        chat_response = client.post(f"/api/steps/{step_response.json()['id']}/chat")
    assert chat_response.status_code == 200, chat_response.text
    return plan_id, int(chat_response.json()["id"])


@pytest.mark.parametrize("source_kind", ["topic", "chat"])
@pytest.mark.parametrize("surface", ["step", "final"])
@pytest.mark.parametrize(
    ("policy", "tool", "waiting"),
    [
        ("manual", "lookup", True),
        ("balanced", "publish", True),
        ("plan", "publish", True),
        ("autonomous", "publish", False),
    ],
)
def test_delegated_discussions_inherit_the_plan_policy(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    source_kind: str,
    surface: str,
    policy: str,
    tool: str,
    waiting: bool,
) -> None:
    source_id = _conversation(client, source_kind, policy)
    _, chat_id = _delegated_discussion(client, source_kind, source_id, policy, surface)
    chat = client.get(f"/api/chats/{chat_id}").json()
    assert chat["approval_policy"] is None

    provider = _ScriptedProvider(_tool_round(_Call("call", f"acme__{tool}")), _answer("Done."))
    executed = _install(monkeypatch, provider)
    events = _send(client, "chat", chat_id)
    assert (events[-1][0] == "approval_required") is waiting
    assert bool(executed) is not waiting
    assert chat["inherited_approval_policy"] == policy


def test_a_delegated_discussion_can_override_and_restore_its_inherited_policy(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    source_id = _conversation(client, "topic", "manual")
    _, chat_id = _delegated_discussion(client, "topic", source_id, "manual", "step")
    assert (
        client.patch(f"/api/chats/{chat_id}", json={"approval_policy": "autonomous"}).status_code
        == 200
    )
    provider = _ScriptedProvider(_tool_round(_Call("one", "acme__lookup")), _answer("Done."))
    assert _install(monkeypatch, provider) == []
    assert _send(client, "chat", chat_id)[-1][0] != "approval_required"

    assert client.patch(f"/api/chats/{chat_id}", json={"approval_policy": None}).status_code == 200
    provider = _ScriptedProvider(_tool_round(_Call("two", "acme__lookup")), _answer("Done."))
    executed = _install(monkeypatch, provider)
    assert _send(client, "chat", chat_id)[-1][0] == "approval_required"
    assert executed == []


@pytest.mark.parametrize("source_kind", ["topic", "chat"])
def test_amendment_updates_existing_discussions_default_without_losing_an_override(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, source_kind: str
) -> None:
    source_id = _conversation(client, source_kind, "manual")
    plan_id, chat_id = _delegated_discussion(client, source_kind, source_id, "manual", "step")
    assert (
        client.patch(f"/api/chats/{chat_id}", json={"approval_policy": "autonomous"}).status_code
        == 200
    )
    assert (
        client.patch(
            _base(source_kind, source_id), json={"approval_policy": "balanced"}
        ).status_code
        == 200
    )
    plan = client.get(f"/api/step-plans/{plan_id}").json()
    amended = client.post(
        f"/api/step-plans/{plan_id}/amend",
        json={
            f"{source_kind}_id": source_id,
            "request": "Add the second part.",
            "steps": [{"title": "Second part", "instruction": "Draft it."}],
            "expected_state_token": plan["state_token"],
        },
    )
    assert amended.status_code == 200, amended.text
    chat = client.get(f"/api/chats/{chat_id}").json()
    assert (chat["approval_policy"], chat["inherited_approval_policy"]) == (
        "autonomous",
        "balanced",
    )

    assert client.patch(f"/api/chats/{chat_id}", json={"approval_policy": None}).status_code == 200
    provider = _ScriptedProvider(_tool_round(_Call("call", "acme__publish")), _answer("Done."))
    executed = _install(monkeypatch, provider)
    assert _send(client, "chat", chat_id)[-1][0] == "approval_required"
    assert executed == []


def test_a_user_started_plan_keeps_the_normal_chat_default(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    source_id = _conversation(client, "topic", "manual")
    _, chat_id = _delegated_discussion(client, "topic", source_id, None, "step")
    assert client.get(f"/api/chats/{chat_id}").json()["inherited_approval_policy"] is None
    provider = _ScriptedProvider(_tool_round(_Call("call", "acme__lookup")), _answer("Done."))
    executed = _install(monkeypatch, provider)
    waiting = client.get("/api/settings").json()["chat_approval_policy"] == "manual"
    assert (_send(client, "chat", chat_id)[-1][0] == "approval_required") is waiting
    assert bool(executed) is not waiting


# -- classification ---------------------------------------------------------


def test_builtin_tables_and_server_hints_decide_read_only() -> None:
    assert is_read_only("precursor", "list_topics")
    assert not is_read_only("precursor", "write_workspace_file")
    assert not is_read_only("fetch", "http_request")
    assert is_read_only("acme", "lookup", _READ)
    assert not is_read_only("acme", "publish", _WRITE)
    assert not is_read_only("acme", "unknown")


def test_gate_reasons_per_policy() -> None:
    args = ("acme__lookup", "acme", "lookup", _READ)
    assert ApprovalGate("autonomous").reason_for(*args) is None
    assert ApprovalGate("balanced").reason_for(*args) is None
    assert ApprovalGate("manual").reason_for(*args) is not None
    write = ("acme__publish", "acme", "publish", _WRITE)
    assert ApprovalGate("balanced").reason_for(*write) is not None
    allow = CompiledRule(id=1, effect="allow", server="acme", tool="publish")
    assert ApprovalGate("balanced", (allow,)).reason_for(*write) is None
    assert ApprovalGate("autonomous").offers_intent
    assert not ApprovalGate("manual").offers_intent


@pytest.mark.parametrize("name", ["create_step_plan", "amend_step_plan", "run_step_plan"])
def test_delegation_respects_the_conversation_policy_and_explicit_rules(name: str) -> None:
    call = (f"precursor__{name}", "precursor", name, None)
    assert not is_read_only("precursor", name)
    assert ApprovalGate("balanced").reason_for(*call) is None
    assert ApprovalGate("plan").reason_for(*call) is None
    assert ApprovalGate("manual").reason_for(*call) is not None
    ask = CompiledRule(id=1, effect="ask", server="precursor", tool=name)
    assert ApprovalGate("balanced", (ask,)).reason_for(*call) is not None


# -- the park / decide / resume loop ----------------------------------------


@pytest.mark.parametrize("kind", ["topic", "chat"])
def test_manual_policy_parks_then_resumes_after_approval(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, kind: str
) -> None:
    provider = _ScriptedProvider(
        _tool_round(_Call("c1", "acme__lookup", '{"q": "x"}')), _answer("All done.")
    )
    executed = _install(monkeypatch, provider)
    cid = _conversation(client, kind, "manual")

    events = _send(client, kind, cid)
    names = [n for n, _ in events]
    assert names[-1] == "approval_required"
    assert "tool_result" not in names
    assert executed == []
    cards = events[-1][1]["approvals"]
    assert [(c["tool_name"], c["status"], c["kind"]) for c in cards] == [
        ("acme__lookup", "pending", "tool")
    ]
    assert "request_approval" not in provider.calls[0]["tools"]

    listed = client.get(f"{_base(kind, cid)}/approvals").json()
    assert [a["id"] for a in listed] == [cards[0]["id"]]
    # Still waiting, so there is nothing to resume yet.
    assert client.post(f"{_base(kind, cid)}/approvals/resume").status_code == 409

    result = _decide(client, kind, cid, cards[0]["id"], approve=True)
    assert result["pending"] == 0
    assert result["approval"]["status"] == "approved"

    resumed = _resume(client, kind, cid)
    resumed_names = [n for n, _ in resumed]
    assert "tool_result" in resumed_names
    assert resumed_names[-1] != "approval_required"
    assert any(n == "delta" and d.get("content") == "All done." for n, d in resumed)
    assert executed == [("acme", "lookup", {"q": "x"})]
    # The next round saw the tool result, so the parked turn hydrated intact.
    roles = [m.role for m in provider.calls[1]["messages"]]
    assert roles[-2:] == ["assistant", "tool"] or "tool" in roles

    assert client.get(f"{_base(kind, cid)}/approvals").json() == []
    assert client.post(f"{_base(kind, cid)}/approvals/resume").status_code == 409


@pytest.mark.parametrize("kind", ["topic", "chat"])
def test_plan_delegation_keeps_the_original_turn_through_approval_resumes(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, kind: str
) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Message, MessageRole

    cid = _conversation(client, kind, "manual")

    class InterleavedProvider(_ScriptedProvider):
        async def stream_chat_with_tools(self, **kwargs: Any) -> AsyncIterator[ProviderEvent]:
            if not self.calls:
                async with SessionLocal() as session:
                    session.add(
                        Message(
                            role=MessageRole.USER,
                            content="A different filed result",
                            **{f"{kind}_id": cid},
                        )
                    )
                    await session.commit()
            async for event in super().stream_chat_with_tools(**kwargs):
                yield event

    provider = InterleavedProvider(
        _tool_round(_Call("create", "precursor__create_step_plan", '{"ask": "Guide"}')),
        _tool_round(_Call("amend", "precursor__amend_step_plan", '{"request": "More"}')),
        _answer("Done."),
    )
    tools = [
        MCPToolDef("precursor", name, "Plan", {"type": "object", "properties": {}})
        for name in ("create_step_plan", "amend_step_plan")
    ]
    executed = _install(monkeypatch, provider, extra_tools=tools)

    events = _send(client, kind, cid, "Exactly two steps; no files.")
    source_id = next(data["id"] for name, data in events if name == "user_message")
    approval = events[-1][1]["approvals"][0]
    assert json.loads(approval["arguments"])["source_message_id"] == source_id
    assert json.loads(approval["arguments"])[f"{kind}_id"] == cid
    _decide(client, kind, cid, approval["id"], approve=True)

    resumed = _resume(client, kind, cid)
    amendment = resumed[-1][1]["approvals"][0]
    assert json.loads(amendment["arguments"])["source_message_id"] == source_id
    assert json.loads(amendment["arguments"])[f"{kind}_id"] == cid
    _decide(client, kind, cid, amendment["id"], approve=True)
    _resume(client, kind, cid)
    assert [args["source_message_id"] for _, _, args in executed] == [source_id, source_id]


def test_deny_with_feedback_skips_the_tool_and_tells_the_model(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    provider = _ScriptedProvider(_tool_round(_Call("c1", "acme__publish")), _answer("Understood."))
    executed = _install(monkeypatch, provider)
    cid = _conversation(client, "topic", "balanced")

    cards = _send(client, "topic", cid)[-1][1]["approvals"]
    _decide(client, "topic", cid, cards[0]["id"], approve=False, feedback="not today")
    _resume(client, "topic", cid)

    assert executed == []
    tool_msgs = [m for m in provider.calls[1]["messages"] if m.role == "tool"]
    assert tool_msgs and "not today" in tool_msgs[-1].content


def test_balanced_runs_reads_and_parks_writes_in_the_same_round(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    provider = _ScriptedProvider(
        _tool_round(_Call("r1", "acme__lookup"), _Call("w1", "acme__publish")), _answer("ok")
    )
    executed = _install(monkeypatch, provider)
    cid = _conversation(client, "chat", "balanced")

    events = _send(client, "chat", cid)
    assert [n for n, _ in events if n == "tool_result"] == ["tool_result"]
    assert events[-1][0] == "approval_required"
    assert executed == [("acme", "lookup", {})]
    assert [c["tool_name"] for c in events[-1][1]["approvals"]] == ["acme__publish"]


def test_autonomous_runs_everything_but_still_offers_request_approval(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    provider = _ScriptedProvider(_tool_round(_Call("w1", "acme__publish")), _answer("ok"))
    executed = _install(monkeypatch, provider)
    cid = _conversation(client, "topic", "autonomous")

    events = _send(client, "topic", cid)
    assert "approval_required" not in [n for n, _ in events]
    assert executed == [("acme", "publish", {})]
    assert "request_approval" in provider.calls[0]["tools"]


def test_model_can_request_approval_for_its_own_intent(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    provider = _ScriptedProvider(
        _tool_round(_Call("i1", "request_approval", '{"reason": "I will wipe the draft"}')),
        _answer("Going ahead."),
    )
    executed = _install(monkeypatch, provider)
    cid = _conversation(client, "chat", "autonomous")

    cards = _send(client, "chat", cid)[-1][1]["approvals"]
    assert (cards[0]["kind"], cards[0]["reason"]) == ("intent", "I will wipe the draft")

    _decide(client, "chat", cid, cards[0]["id"], approve=True)
    _resume(client, "chat", cid)

    assert executed == []
    tool_msgs = [m for m in provider.calls[1]["messages"] if m.role == "tool"]
    assert tool_msgs and "approved" in tool_msgs[-1].content.lower()


def test_always_allow_covers_later_calls_in_the_conversation(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    provider = _ScriptedProvider(
        _tool_round(_Call("w1", "acme__publish")),
        _tool_round(_Call("w2", "acme__publish")),
        _answer("done"),
    )
    executed = _install(monkeypatch, provider)
    cid = _conversation(client, "topic", "balanced")

    cards = _send(client, "topic", cid)[-1][1]["approvals"]
    _decide(client, "topic", cid, cards[0]["id"], approve=True, scope="conversation")
    resumed = _resume(client, "topic", cid)

    assert "approval_required" not in [n for n, _ in resumed]
    assert [e[1] for e in executed] == ["publish", "publish"]


# -- persistence and the badge ----------------------------------------------


def test_pending_approvals_surface_on_the_sidebar_lists_and_survive_a_new_message(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    provider = _ScriptedProvider(
        _tool_round(_Call("w1", "acme__publish")),
        _tool_round(_Call("w2", "acme__publish")),
        _answer("moved on"),
    )
    _install(monkeypatch, provider)
    tid = _conversation(client, "topic", "manual")
    cid = _conversation(client, "chat", "manual")

    _send(client, "topic", tid)
    _send(client, "chat", cid)

    topics = client.get("/api/topics/tree").json()
    node = next(n for n in _walk(topics) if n["id"] == tid)
    assert node["pending_approvals"] == 1
    chat = next(c for c in client.get("/api/chats").json() if c["id"] == cid)
    assert chat["pending_approvals"] == 1

    # Writing a new message moves on: the open card is cancelled, not left dangling.
    _send(client, "topic", tid, "never mind")
    assert client.get(f"{_base('topic', tid)}/approvals").json() == []
    node = next(n for n in _walk(client.get("/api/topics/tree").json()) if n["id"] == tid)
    assert node["pending_approvals"] == 0


def test_clearing_the_transcript_drops_its_approvals(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    provider = _ScriptedProvider(_tool_round(_Call("w1", "acme__publish")))
    _install(monkeypatch, provider)
    cid = _conversation(client, "chat", "manual")
    _send(client, "chat", cid)
    assert len(client.get(f"{_base('chat', cid)}/approvals").json()) == 1

    assert client.delete(f"{_base('chat', cid)}/messages").status_code in (200, 204)

    assert client.get(f"{_base('chat', cid)}/approvals").json() == []


def test_deciding_twice_conflicts(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    provider = _ScriptedProvider(_tool_round(_Call("w1", "acme__publish")))
    _install(monkeypatch, provider)
    cid = _conversation(client, "topic", "manual")
    card = _send(client, "topic", cid)[-1][1]["approvals"][0]

    _decide(client, "topic", cid, card["id"], approve=False)
    again = client.post(f"{_base('topic', cid)}/approvals/{card['id']}", json={"approve": True})
    assert again.status_code == 409


def test_policy_is_validated_and_defaults_to_the_global_setting(client: TestClient) -> None:
    cid = _conversation(client, "chat", None)
    assert client.get(f"/api/chats/{cid}").json()["approval_policy"] is None
    bad = client.patch(f"/api/chats/{cid}", json={"approval_policy": "reckless"})
    assert bad.status_code == 422
    ok = client.patch(f"/api/chats/{cid}", json={"approval_policy": "manual"})
    assert ok.json()["approval_policy"] == "manual"
    defaults = client.get("/api/settings").json()
    assert defaults["topic_approval_policy"] == defaults["chat_approval_policy"] == "autonomous"
    ignored = client.put("/api/settings", json={"topic_approval_policy": "reckless"}).json()
    assert ignored["topic_approval_policy"] == "autonomous"
    saved = client.put("/api/settings", json={"chat_approval_policy": "manual"}).json()
    assert (saved["chat_approval_policy"], saved["topic_approval_policy"]) == (
        "manual",
        "autonomous",
    )


def _walk(nodes: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for n in nodes:
        yield n
        yield from _walk(n.get("children", []))
