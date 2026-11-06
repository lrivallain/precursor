"""The assessor's read-only tools: what it may open, and what it can't."""

from __future__ import annotations

import json
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import Chat, Message, MessageRole, StepPlan
from precursor.backend.models.message import MESSAGE_KIND_STEP_AUTOMATION
from precursor.backend.services import (
    approval_rules,
    step_assessor_tools,
    step_evidence,
    step_plan_llm,
    turn_engine,
)
from precursor.backend.services import approvals as approval_service
from precursor.backend.services.context_budget import estimate_total_tokens
from precursor.backend.services.conversation_turn import TurnSettings
from precursor.backend.services.llm.base import (
    ChatMessage,
    TextDeltaEvent,
    ToolCallRequest,
    ToolCallsEvent,
    TurnDoneEvent,
)
from precursor.backend.services.mcp.client import MCPToolDef
from precursor.backend.services.message_links import MESSAGE_LINK_INSTRUCTION
from precursor.backend.services.step_assessor_tools import Inspector
from precursor.backend.services.turn_engine import ToolResultTurn


def _verdict(score: int, summary: str) -> str:
    return json.dumps(
        {
            "score": float(score),
            "summary": summary,
            "covered": [],
            "evidence": [],
            "assumptions": [],
            "observations": [],
            "gaps": [],
            "contradictions": [],
        }
    )


_VERDICT = _verdict(81, "ok")


def test_an_assessment_without_a_score_is_not_treated_as_zero() -> None:
    with pytest.raises(HTTPException, match="didn't return a score"):
        step_plan_llm.parse_assessment('{"summary": "Missing score"}')


@pytest.mark.parametrize(
    "changes",
    [
        {"score": True},
        {"summary": {"unexpected": "object"}},
        {"summary": ""},
        {"gaps": "Missing evidence"},
        {"contradictions": [42]},
    ],
)
def test_invalid_assessment_fields_are_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(HTTPException):
        step_plan_llm.parse_assessment(json.dumps({**json.loads(_VERDICT), **changes}))


@pytest.mark.parametrize("answer", ['{"score": 80}', "Not a structured verdict"])
def test_incomplete_or_unstructured_verdicts_are_rejected(answer: str) -> None:
    with pytest.raises(HTTPException):
        step_plan_llm.parse_assessment(answer)


@pytest.fixture(autouse=True, scope="module")
def _db() -> None:
    with TestClient(create_app()):
        pass


def _docx(path: Path) -> Path:
    body = (
        "<w:document><w:body><w:p><w:r><w:t>Runbook</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Fail over &amp; verify</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", body)
    return path


def _pptx(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for i in (2, 10, 1):
            z.writestr(f"ppt/slides/slide{i}.xml", f"<p><a:t>Slide text {i}</a:t></p>")
    return path


def _call(name: str, **args: Any) -> ToolCallRequest:
    return ToolCallRequest(id="1", name=name, arguments=json.dumps(args))


async def _run(inspector: Inspector, name: str, **args: Any) -> str:
    return await inspector.run(_call(name, **args))


async def test_office_files_are_read_as_text_in_slide_order(tmp_path: Path) -> None:
    docx, pptx = _docx(tmp_path / "a.docx").resolve(), _pptx(tmp_path / "b.pptx").resolve()
    inspector = Inspector([str(docx), str(pptx)], [])
    text = await _run(inspector, "read_file", path=str(docx))
    assert "Runbook" in text and "Fail over & verify" in text
    assert any(str(docx) in line for line in inspector.log)
    deck = await _run(inspector, "read_file", path=str(pptx))
    assert deck.index("Slide text 1") < deck.index("Slide text 2") < deck.index("Slide text 10")
    assert any("a.docx" in line for line in inspector.log)


def test_assessor_tool_log_keeps_workspace_paths_and_sanitizes_fetch_urls() -> None:
    workspace_call = _call(
        "precursor__read_workspace_file",
        workspace_id=6,
        path="azure-container-apps-brief.html",
    )
    assert step_assessor_tools._tool_target(workspace_call) == (
        " (workspace_id=6, path=azure-container-apps-brief.html)"
    )
    fetch_call = _call(
        "fetch__http_get",
        url="https://learn.microsoft.com/en-us/azure/container-apps/overview?token=secret",
    )
    target = step_assessor_tools._tool_target(fetch_call)
    assert target == " (https://learn.microsoft.com/en-us/azure/container-apps/overview)"
    assert "secret" not in target


async def test_only_files_the_plan_produced_can_be_read(tmp_path: Path) -> None:
    mine, other = tmp_path / "mine.md", tmp_path / "secret.md"
    mine.write_text("# Mine")
    other.write_text("TOP-SECRET")
    inspector = Inspector([str(mine.resolve())], [])
    assert "Mine" in await _run(inspector, "read_file", path=str(mine))
    refused = await _run(inspector, "read_file", path=str(other))
    assert "Not allowed" in refused and "TOP-SECRET" not in refused
    assert "Not allowed" in await _run(inspector, "read_file", path="/etc/hosts")
    # A path that climbs out of the allowed one is resolved before it is judged.
    sneaky = f"{tmp_path}/sub/../secret.md"
    assert "TOP-SECRET" not in await _run(inspector, "read_file", path=sneaky)
    listing = await _run(inspector, "list_files")
    assert "mine.md" in listing and "secret.md" not in listing


async def test_a_long_file_is_paged(tmp_path: Path) -> None:
    big = tmp_path / "big.txt"
    big.write_text("a" * 9000 + "TAIL")
    inspector = Inspector([str(big.resolve())], [])
    first = await _run(inspector, "read_file", path=str(big), length=5000)
    assert "offset=5000" in first and "TAIL" not in first
    assert "TAIL" in await _run(inspector, "read_file", path=str(big), offset=9000)


async def test_a_tool_calls_full_output_is_readable_by_its_number() -> None:
    inspector = Inspector([], ["first", "x" * 20000])
    assert await _run(inspector, "read_tool_output", index=1) == "first"
    truncated = await _run(inspector, "read_tool_output", index=2)
    assert truncated.startswith("x" * 12000) and "Output truncated" in truncated
    assert inspector.log == [
        "Read the full output of tool call 1",
        "Read the first 12000 characters of tool call 2",
    ]
    assert "no tool call number 3" in await _run(inspector, "read_tool_output", index=3)


async def test_a_bad_call_is_reported_not_raised() -> None:
    inspector = Inspector([], [])
    assert "Unknown tool" in await _run(inspector, "rm")
    assert "Invalid" in await inspector.run(
        ToolCallRequest(id="1", name="read_file", arguments="{")
    )
    assert "Invalid" in await _run(inspector, "read_tool_output", index="x")
    assert not inspector.useful


async def test_the_workspace_folder_of_a_built_file_can_be_browsed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from precursor.backend.config import get_settings

    root = tmp_path / "workspaces"
    (root / "deck").mkdir(parents=True)
    built, extra = root / "deck" / "index.html", root / "deck" / "notes.md"
    built.write_text("<html></html>")
    extra.write_text("Notes")
    monkeypatch.setitem(get_settings().__dict__, "workspaces_dir", str(root.resolve()))
    inspector = Inspector([str(built.resolve())], [])
    assert "notes.md" in await _run(inspector, "list_files")
    assert "Notes" in await _run(inspector, "read_file", path=str(extra))


async def test_collect_hands_the_assessor_the_files_and_outputs(tmp_path: Path) -> None:
    deck = tmp_path / "deck.html"
    deck.write_text("<html><body><section class='slide'>Hi</section></body></html>")
    ev = step_evidence.inspect_file(str(deck), produced=True, asked=None)
    assert ev.files == [str(deck.resolve())]
    assert step_evidence.inspect_file(str(deck), produced=False, asked=None).files == []


async def test_recorded_tool_trace_is_bounded_and_does_not_prove_absence() -> None:
    async with SessionLocal() as session:
        chat = Chat(title="Evidence trace", slug=f"trace-{uuid4().hex}")
        session.add(chat)
        await session.flush()
        for i in range(14):
            session.add(
                Message(
                    chat_id=chat.id,
                    role=MessageRole.TOOL,
                    content=f"result {i}",
                    tool_calls=json.dumps({"name": "workiq__get_draft", "arguments": "{}"}),
                )
            )
        await session.commit()
        ev = await step_evidence.collect(session, texts=[], chat_ids=[chat.id, chat.id], ask="")
        assert ev.trace_conversations == 1
        assert ev.trace_truncated
        assert len(ev.trace) == 12
        assert ev.outputs == [f"result {i}" for i in range(2, 14)]
        assert "result 2" in ev.trace[0] and "result 13" in ev.trace[-1]
        assert "cannot prove that an unlisted action did or did not happen" in ev.prompt()
        assert "responses were omitted" in ev.prompt()

        no_trace = await step_evidence.collect(
            session, texts=[], chat_ids=[chat.id], ask="", with_trace=False
        )
        assert no_trace.trace == [] and no_trace.outputs == [] and no_trace.prompt() == ""


async def test_empty_recorded_tool_trace_is_explicitly_inconclusive() -> None:
    async with SessionLocal() as session:
        chat = Chat(title="Empty trace", slug=f"trace-{uuid4().hex}")
        session.add(chat)
        await session.commit()
        ev = await step_evidence.collect(session, texts=[], chat_ids=[chat.id], ask="")
    assert ev.trace_conversations == 1 and not ev.trace_truncated
    assert "No tool responses were found" in ev.prompt()
    assert "not a complete audit" in ev.prompt()


def test_a_workspace_deep_link_is_read_as_the_workspaces_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from precursor.backend.config import get_settings

    root = tmp_path / "workspaces"
    (root / "brief").mkdir(parents=True)
    page = root / "brief" / "brief.html"
    page.write_text("<html><body><h1>Brief</h1></body></html>")
    monkeypatch.setitem(get_settings().__dict__, "workspaces_dir", str(root.resolve()))
    ev = step_evidence.inspect_file("/ws/brief/brief.html", produced=True, asked=None)
    assert ev.cap is None and ev.files == [str(page.resolve())]
    assert step_evidence.existing_paths(["Saved to /ws/brief/brief.html"]) == [
        "/ws/brief/brief.html"
    ]
    gone = step_evidence.inspect_file("/ws/brief/nope.html", produced=True, asked=None)
    assert gone.cap is not None


def test_an_html_page_with_sections_is_not_called_a_deck(tmp_path: Path) -> None:
    page = tmp_path / "brief.html"
    page.write_text(
        "<html><body><section><h2>One</h2><p>a</p></section>"
        "<section><h2>Two</h2><p>b</p></section></body></html>"
    )
    labels = [
        c.label for c in step_evidence.inspect_file(str(page), produced=True, asked=None).checks
    ]
    assert "The page holds 2 section(s)" in labels
    assert not any("deck" in label or "Slide" in label for label in labels)


def _plan() -> Any:
    return SimpleNamespace(source_topic_id=None, source_chat_id=None, llm_selection=None)


class _Active:
    def __init__(self, tools: list[MCPToolDef]) -> None:
        self.tools = tools
        self.tool_to_server = {t.qualified_name: (t.server, t.name) for t in tools}
        self.restricted: dict[str, frozenset[str]] = {}

    def restrict_server_tools(self, server: str, allowed: Any) -> None:
        self.restricted[server] = frozenset(allowed)


def _mail_tools() -> list[MCPToolDef]:
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    return [
        MCPToolDef("workiq", "get_draft", "Read a draft", schema, read_only=True),
        MCPToolDef("workiq", "send_mail", "Send", schema, read_only=False),
    ]


class _Script:
    """A provider that plays one scripted round per call and records what it was given."""

    name = "scripted"

    def __init__(self, rounds: list[list[ToolCallRequest] | str]) -> None:
        self.rounds = list(rounds)
        self.tools_seen: list[list[str]] = []
        self.messages_seen: list[list[Any]] = []
        self.models_seen: list[str] = []

    async def stream_chat(self, **_: Any) -> Any:
        yield ""

    async def stream_chat_with_tools(
        self, *, model: str, messages: Any, tools: Any, **_: Any
    ) -> Any:
        self.tools_seen.append([t.name for t in tools])
        self.messages_seen.append(list(messages))
        self.models_seen.append(model)
        step = self.rounds.pop(0)
        if isinstance(step, str):
            yield TextDeltaEvent(content=step)
        else:
            yield ToolCallsEvent(calls=step)
        yield TurnDoneEvent(finish_reason="stop")

    async def list_models(self) -> list[Any]:
        return []


@pytest.fixture
def chat(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A fake MCP manager serving a mailbox plus the chat settings it runs with."""
    seen: dict[str, Any] = {
        "ran": [],
        "active": _Active(_mail_tools()),
        "gate": approval_service.ApprovalGate(policy=approval_service.POLICY_BALANCED),
        "rounds": 6,
        "history": [],
    }

    class _Manager:
        @asynccontextmanager
        async def acquired(self, servers: list[str], **_: Any) -> Any:
            seen["servers"] = servers
            yield seen["active"]

    async def _settings(_session: Any, **kwargs: Any) -> TurnSettings:
        seen["resolved"] = kwargs
        return TurnSettings(
            model="m",
            reasoning_effort="",
            max_tool_rounds=seen["rounds"],
            max_input_tokens=seen.get("input_tokens", 100_000),
            max_tool_result_tokens=seen.get("tool_result_tokens", 10_000),
            provider=seen["provider"],
            github_token="",
            enabled_servers=seen.get("enabled_servers", ["workiq"]),
            gate=seen["gate"],
        )

    async def _execute(_active: Any, call: ToolCallRequest, _wait: float) -> Any:
        seen["ran"].append(call.name)
        yield ToolResultTurn(
            call,
            seen.get("responses", {}).get(call.id, "Subject: Q3 plan"),
            call.id in seen.get("failed_calls", set()),
            None,
        )

    async def _history(_session: Any, kind: str, container_id: int) -> list[ChatMessage]:
        seen["history_requested"] = (kind, container_id)
        return list(seen["history"])

    monkeypatch.setattr(step_assessor_tools, "get_mcp_client_manager", lambda: _Manager())
    monkeypatch.setattr(step_assessor_tools, "resolve_turn_settings", _settings)
    monkeypatch.setattr(step_assessor_tools, "snapshot_history", _history)
    monkeypatch.setattr(turn_engine, "execute_tool_call", _execute)
    return seen


async def _review(
    inspector: Inspector,
    *,
    chat_id: int | None = 7,
    topic_id: int | None = None,
    user: str = "usr",
) -> str:
    async with SessionLocal() as session:
        return await step_assessor_tools.review_with_chat_tools(
            session,
            chat_id=chat_id,
            topic_id=topic_id,
            system="SYS",
            user=user,
            inspector=inspector,
            usage_source="step_assessment",
        )


def test_the_assessor_gate_is_never_looser_than_balanced() -> None:
    gate = approval_service.ApprovalGate
    assert step_assessor_tools.assessor_gate(None).policy == approval_service.POLICY_BALANCED
    loose = step_assessor_tools.assessor_gate(gate(policy=approval_service.POLICY_AUTONOMOUS))
    assert loose.policy == approval_service.POLICY_BALANCED
    manual = gate(policy=approval_service.POLICY_MANUAL)
    assert step_assessor_tools.assessor_gate(manual).policy == approval_service.POLICY_MANUAL


@pytest.mark.parametrize(
    "name",
    ["create_step_plan", "amend_step_plan", "edit_step_plan", "run_step_plan", "pause_step_plan"],
)
def test_assessor_blocks_plan_mutations_without_an_allow_rule(name: str) -> None:
    call = (f"precursor__{name}", "precursor", name, None)
    assert not approval_service.is_read_only("precursor", name)
    for policy in (
        approval_service.POLICY_AUTONOMOUS,
        approval_service.POLICY_BALANCED,
        approval_service.POLICY_PLAN,
        approval_service.POLICY_MANUAL,
    ):
        gate = step_assessor_tools.assessor_gate(approval_service.ApprovalGate(policy=policy))
        assert gate.reason_for(*call) is not None
    allow = approval_rules.CompiledRule(id=1, effect="allow", server="precursor", tool=name)
    gate = step_assessor_tools.assessor_gate(
        approval_service.ApprovalGate(policy=approval_service.POLICY_AUTONOMOUS, rules=(allow,))
    )
    assert gate.reason_for(*call) is None


async def test_the_assessor_reads_with_a_chats_tools_but_never_changes(
    chat: dict[str, Any],
) -> None:
    chat["provider"] = provider = _Script(
        [
            [
                ToolCallRequest("1", "workiq__get_draft", "{}"),
                ToolCallRequest("2", "workiq__send_mail", "{}"),
            ],
            _VERDICT,
        ]
    )
    # The plan is "autonomous": the assessor must still treat it as balanced.
    chat["gate"] = approval_service.ApprovalGate(policy=approval_service.POLICY_AUTONOMOUS)
    inspector = Inspector([], [])
    assert await _review(inspector) == _VERDICT

    assert chat["ran"] == ["workiq__get_draft"]
    assert chat["resolved"] == {"chat_id": 7, "topic_id": None}
    assert chat["servers"] == ["workiq"]
    assert chat["active"].restricted == {}
    first = provider.tools_seen[0]
    assert not {"list_files", "read_file", "read_tool_output"} & set(first)
    assert "workiq__get_draft" in first and "workiq__send_mail" in first
    assert approval_service.REQUEST_APPROVAL_TOOL not in first
    answered = [m.content for m in provider.messages_seen[1] if m.role == "tool"]
    assert answered[0] == "Subject: Q3 plan" and answered[1].startswith("Not run:")
    assert "assumptions" in answered[1]
    assert inspector.log == [
        "Called workiq__get_draft (workiq): ok",
        "Declined workiq__send_mail (workiq): needs approval",
    ]


@pytest.mark.parametrize("allow_write", [False, True])
async def test_assessor_has_exposed_precursor_tools_under_the_review_gate(
    chat: dict[str, Any], allow_write: bool
) -> None:
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    chat["active"] = _Active(
        [
            MCPToolDef("precursor", "get_topic", "Read a topic", schema, read_only=True),
            MCPToolDef("precursor", "post_message", "Post a message", schema, read_only=False),
        ]
    )
    chat["enabled_servers"] = ["precursor"]
    if allow_write:
        rule = approval_rules.CompiledRule(
            id=1, effect=approval_service.RULE_ALLOW, server="precursor", tool="post_message"
        )
        chat["gate"] = approval_service.ApprovalGate(
            policy=approval_service.POLICY_AUTONOMOUS, rules=(rule,)
        )
    else:
        chat["gate"] = approval_service.ApprovalGate(policy=approval_service.POLICY_AUTONOMOUS)
    chat["provider"] = provider = _Script(
        [
            [
                ToolCallRequest("1", "precursor__get_topic", "{}"),
                ToolCallRequest("2", "precursor__post_message", "{}"),
            ],
            _VERDICT,
        ]
    )
    inspector = Inspector([], [])
    assert await _review(inspector) == _VERDICT
    assert chat["servers"] == ["precursor"]
    assert chat["active"].restricted == {}
    assert {"precursor__get_topic", "precursor__post_message"} <= set(provider.tools_seen[0])
    assert chat["ran"] == (
        ["precursor__get_topic", "precursor__post_message"]
        if allow_write
        else ["precursor__get_topic"]
    )
    assert inspector.log[0] == "Called precursor__get_topic (precursor): ok"
    if allow_write:
        assert inspector.log[1] == "Called precursor__post_message (precursor): ok"
    else:
        assert inspector.log[1] == "Declined precursor__post_message (precursor): needs approval"


async def test_a_topic_without_a_step_chat_resolves_from_the_topic(chat: dict[str, Any]) -> None:
    chat["provider"] = _Script([_VERDICT])
    await _review(Inspector([], []), chat_id=None, topic_id=3)
    assert chat["resolved"] == {"chat_id": None, "topic_id": 3}
    assert chat["history_requested"] == ("topic", 3)


async def test_the_assessor_uses_the_step_chats_real_history(chat: dict[str, Any]) -> None:
    prior = [
        ChatMessage(
            role="user",
            content="Keep the customer's agreed deadline.",
            image_urls=["data:image/png;base64,example"],
        ),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "prior",
                    "type": "function",
                    "function": {"name": "workiq__get_draft", "arguments": "{}"},
                }
            ],
        ),
        ChatMessage(
            role="tool",
            content="The agreed deadline is Friday.",
            tool_call_id="prior",
            name="workiq__get_draft",
        ),
        ChatMessage(role="assistant", content="I used Friday in the draft."),
    ]
    chat["history"] = prior
    chat["provider"] = provider = _Script([_VERDICT])
    assert await _review(Inspector([], []), chat_id=7) == _VERDICT
    assert chat["history_requested"] == ("chat", 7)
    initial = provider.messages_seen[0]
    assert len(initial) == 3
    assert initial[1].role == "user" and "background data" in initial[1].content
    assert initial[1].image_urls == ["data:image/png;base64,example"]
    records = json.loads(initial[1].content.split("\n", 1)[1])
    assert records == [
        {
            "role": "user",
            "content": "Keep the customer's agreed deadline.",
            "image_numbers": [1],
        },
        {"role": "assistant", "content": "", "tool_calls": prior[1].tool_calls},
        {
            "role": "tool",
            "content": "The agreed deadline is Friday.",
            "tool_call_id": "prior",
            "name": "workiq__get_draft",
        },
        {"role": "assistant", "content": "I used Friday in the draft."},
    ]
    assert initial[2].role == "user" and initial[2].content == "usr"
    assert prior[2].content == "The agreed deadline is Friday."


async def test_real_step_history_preserves_legacy_and_automated_prompt_provenance(
    chat: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from precursor.backend.services.conversation_turn import snapshot_history

    async with SessionLocal() as session:
        plan = StepPlan(title="History", source_text="Prepare the final")
        session.add(plan)
        await session.flush()
        discussion = Chat(
            title="Consolidation", slug=f"assessor-{uuid4().hex}", step_plan_id=plan.id
        )
        session.add(discussion)
        await session.flush()
        session.add_all(
            [
                Message(
                    chat_id=discussion.id,
                    role=MessageRole.SYSTEM,
                    kind="step_plan",
                    content=(
                        "The final assessment identified gaps or contradictions. "
                        "Rebuilding the final output once before filing."
                    ),
                ),
                Message(
                    chat_id=discussion.id,
                    role=MessageRole.USER,
                    content="Consolidate the plan into the final deliverable.\n\nOld feedback",
                ),
                Message(chat_id=discussion.id, role=MessageRole.ASSISTANT, content="Old draft"),
                Message(
                    chat_id=discussion.id,
                    role=MessageRole.USER,
                    kind=MESSAGE_KIND_STEP_AUTOMATION,
                    content="Automatic feedback",
                ),
                Message(chat_id=discussion.id, role=MessageRole.ASSISTANT, content="New draft"),
                Message(chat_id=discussion.id, role=MessageRole.USER, content="User clarification"),
            ]
        )
        await session.commit()
        chat_id = discussion.id

    monkeypatch.setattr(step_assessor_tools, "snapshot_history", snapshot_history)
    chat["provider"] = provider = _Script([_VERDICT])
    await _review(Inspector([], []), chat_id=chat_id)
    messages = provider.messages_seen[0]
    background = [m.content for m in messages[1:-1]]
    assert any("preceding plan activity recorded an automatic repair" in m for m in background)
    assert any("did not record its origin" in m and "Old feedback" in m for m in background)
    assert any(
        "automation initiated this draft" in m and "Automatic feedback" in m for m in background
    )
    assert any("User clarification" in m for m in background)
    assert messages[-1].content == "usr"


async def test_assessment_history_keeps_normal_tool_result_limits(chat: dict[str, Any]) -> None:
    chat["history"] = [
        ChatMessage(role="user", content="Inspect the draft"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "prior",
                    "type": "function",
                    "function": {"name": "workiq__get_draft", "arguments": "{}"},
                }
            ],
        ),
        ChatMessage(
            role="tool", content="x" * 1000, tool_call_id="prior", name="workiq__get_draft"
        ),
        ChatMessage(role="assistant", content="Draft checked"),
    ]
    chat["tool_result_tokens"] = 20
    chat["provider"] = provider = _Script([_VERDICT])
    await _review(Inspector([], []))
    background = provider.messages_seen[0][1].content
    assert "[truncated" in background and "x" * 61 not in background
    assert "Draft checked" in background


async def test_an_allow_rule_lets_the_assessor_run_a_change(chat: dict[str, Any]) -> None:
    rule = approval_rules.CompiledRule(
        id=1, effect=approval_service.RULE_ALLOW, server="workiq", tool="send_*"
    )
    chat["gate"] = approval_service.ApprovalGate(
        policy=approval_service.POLICY_AUTONOMOUS, rules=(rule,)
    )
    chat["provider"] = _Script([[ToolCallRequest("1", "workiq__send_mail", "{}")], _VERDICT])
    assert await _review(Inspector([], [])) == _VERDICT
    assert chat["ran"] == ["workiq__send_mail"]


async def test_the_assessor_opens_the_file_through_the_loop(
    chat: dict[str, Any], tmp_path: Path
) -> None:
    doc = _docx(tmp_path / "runbook.docx").resolve()
    chat["provider"] = provider = _Script(
        [
            [ToolCallRequest("1", "read_file", json.dumps({"path": str(doc)}))],
            _VERDICT,
        ]
    )
    chat["active"] = _Active([])
    inspector = Inspector([str(doc)], [])
    assert await _review(inspector) == _VERDICT
    assert provider.tools_seen[0] == ["list_files", "read_file"]
    tool_msg = next(m.content for m in provider.messages_seen[1] if m.role == "tool")
    assert "Runbook" in tool_msg
    assert chat["ran"] == []


@pytest.mark.parametrize("name", ["list_files", "read_file", "read_tool_output"])
@pytest.mark.parametrize("restriction", ["manual", "ask"])
async def test_inspector_calls_follow_the_review_gate(
    chat: dict[str, Any], tmp_path: Path, name: str, restriction: str
) -> None:
    file = (tmp_path / "artifact.txt").resolve()
    file.write_text("File contents")
    inspector = Inspector([str(file)], ["Tool output"])
    args = {"read_file": {"path": str(file)}, "read_tool_output": {"index": 1}}.get(name, {})
    if restriction == "manual":
        chat["gate"] = approval_service.ApprovalGate(policy=approval_service.POLICY_MANUAL)
    else:
        rule = approval_rules.CompiledRule(id=1, effect="ask", server="step-inspector", tool=name)
        chat["gate"] = approval_service.ApprovalGate(
            policy=approval_service.POLICY_BALANCED, rules=(rule,)
        )
    chat["provider"] = provider = _Script(
        [[ToolCallRequest("1", name, json.dumps(args))], _VERDICT]
    )
    assert await _review(inspector) == _VERDICT
    result = next(m.content for m in provider.messages_seen[1] if m.role == "tool")
    assert result.startswith("Not run:") and "assumptions" in result
    assert "File contents" not in result and "Tool output" not in result
    assert inspector.log == [f"Declined {name} (step-inspector): needs approval"]


async def test_inspector_allow_rule_can_scope_a_manual_file_read(
    chat: dict[str, Any], tmp_path: Path
) -> None:
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    allowed = (allowed_dir / "allowed.txt").resolve()
    denied = (tmp_path / "other.txt").resolve()
    allowed.write_text("Allowed content")
    denied.write_text("Private content")
    rule = approval_rules.CompiledRule(
        id=1,
        effect="allow",
        server="step-inspector",
        tool="read_file",
        clauses=(approval_rules.ArgClause("path", "under", str(allowed_dir)),),
    )
    chat["gate"] = approval_service.ApprovalGate(
        policy=approval_service.POLICY_MANUAL, rules=(rule,)
    )
    chat["provider"] = provider = _Script(
        [
            [
                ToolCallRequest("1", "read_file", json.dumps({"path": str(allowed)})),
                ToolCallRequest("2", "read_file", json.dumps({"path": str(denied)})),
            ],
            _VERDICT,
        ]
    )
    inspector = Inspector([str(allowed), str(denied)], [])
    assert await _review(inspector) == _VERDICT
    results = [m.content for m in provider.messages_seen[1] if m.role == "tool"]
    assert "Allowed content" in results[0]
    assert results[1].startswith("Not run:") and "Private content" not in results[1]
    assert any("Declined read_file (step-inspector)" in line for line in inspector.log)


async def test_the_assessor_only_gets_relevant_inspection_tools(chat: dict[str, Any]) -> None:
    chat["provider"] = provider = _Script(
        [[ToolCallRequest("1", "read_tool_output", '{"index": 1}')], _VERDICT]
    )
    chat["active"] = _Active([])
    assert await _review(Inspector([], ["tool output"])) == _VERDICT
    assert provider.tools_seen[0] == ["read_tool_output"]
    assert next(m.content for m in provider.messages_seen[1] if m.role == "tool") == "tool output"


async def test_without_tools_the_assessor_still_uses_the_chats_model(
    chat: dict[str, Any],
) -> None:
    chat["provider"] = provider = _Script([_VERDICT])
    chat["active"] = _Active([])
    assert await _review(Inspector([], [])) == _VERDICT
    assert provider.tools_seen == [[]]
    assert provider.models_seen == ["m"]


async def test_the_last_round_withdraws_the_tools_so_the_assessor_answers(
    chat: dict[str, Any],
) -> None:
    chat["rounds"] = 2
    chat["provider"] = provider = _Script(
        [[ToolCallRequest("1", "workiq__get_draft", "{}")], _VERDICT]
    )
    assert await _review(Inspector([], [])) == _VERDICT
    assert provider.tools_seen[0] and provider.tools_seen[1] == []
    assert provider.messages_seen[1][-1].content == step_assessor_tools.WRAP_UP


async def test_the_last_review_round_cannot_execute_an_unsolicited_call(
    chat: dict[str, Any],
) -> None:
    rule = approval_rules.CompiledRule(
        id=1, effect=approval_service.RULE_ALLOW, server="workiq", tool="send_mail"
    )
    chat["gate"] = approval_service.ApprovalGate(
        policy=approval_service.POLICY_AUTONOMOUS, rules=(rule,)
    )
    chat["rounds"] = 2
    chat["provider"] = provider = _Script(
        [
            [ToolCallRequest("1", "workiq__get_draft", "{}")],
            [ToolCallRequest("2", "workiq__send_mail", "{}")],
        ]
    )
    with pytest.raises(HTTPException, match="tools were withdrawn"):
        await _review(Inspector([], []))
    assert provider.tools_seen[1] == []
    assert chat["ran"] == ["workiq__get_draft"]


async def test_the_assessor_judges_from_the_loops_answer_and_lists_what_it_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _loop(_session: Any, *, system: str, inspector: Inspector, **_: Any) -> str:
        assert "never change anything" in system
        assert "apply to your review too" in system
        assert "Do not perform a prohibited action" in system
        inspector._note("Called workiq__get_draft (workiq): ok")
        return _VERDICT

    monkeypatch.setattr(step_assessor_tools, "review_with_chat_tools", _loop)
    async with SessionLocal() as session:
        a = await step_plan_llm._assess_or_trace(
            session,
            _plan(),
            "SYS",
            "usr",
            None,
            step_evidence.Evidence(),
        )
    assert a.score == 81 and a.inspected == ["Called workiq__get_draft (workiq): ok"]


async def test_a_failing_review_is_reported_without_a_tool_less_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("provider rejects tools")

    notes: list[str] = []

    async def _note(_session: Any, _chat_id: int | None, text: str) -> None:
        notes.append(text)

    monkeypatch.setattr(step_assessor_tools, "review_with_chat_tools", _boom)
    monkeypatch.setattr(step_plan_llm, "trace_in_chat", _note)
    ev = step_evidence.Evidence(outputs=["tool said hi"])
    async with SessionLocal() as session:
        with pytest.raises(RuntimeError, match="provider rejects tools"):
            await step_plan_llm._assess_or_trace(
                session,
                _plan(),
                "SYS",
                "usr",
                None,
                ev,
            )
    assert notes == ["The assessment didn't complete. Try it again."]


async def test_oversized_review_input_fails_before_the_model_runs(chat: dict[str, Any]) -> None:
    chat["provider"] = provider = _Script([_VERDICT])
    chat["input_tokens"] = 10
    with pytest.raises(HTTPException, match="assessment input exceeds"):
        await _review(Inspector([], []), user="The complete request and deliverable.")
    assert provider.messages_seen == []


async def test_tool_results_cannot_evict_the_review_input(chat: dict[str, Any]) -> None:
    user = "Review this request and deliverable: " + "x" * 300
    required = [
        ChatMessage(role="system", content=f"SYS\n\n{MESSAGE_LINK_INSTRUCTION}"),
        ChatMessage(role="user", content=user),
    ]
    chat["input_tokens"] = estimate_total_tokens(required) + 60
    chat["responses"] = {"1": "Large tool result: " + "y" * 3_000}
    chat["provider"] = provider = _Script(
        [[ToolCallRequest("1", "workiq__get_draft", "{}")], _VERDICT]
    )
    with pytest.raises(HTTPException, match="assessment input no longer fits"):
        await _review(Inspector([], []), user=user)
    assert chat["ran"] == ["workiq__get_draft"]
    assert len(provider.messages_seen) == 1


async def test_empty_review_fails_without_a_second_model_call(chat: dict[str, Any]) -> None:
    chat["provider"] = provider = _Script([""])
    with pytest.raises(HTTPException, match="did not return an assessment"):
        await _review(Inspector([], []))
    assert len(provider.messages_seen) == 1


def test_consolidation_resolves_only_evidenced_mechanical_conflicts() -> None:
    prompt = step_plan_llm._FINAL_DRAFT_PROMPT
    assert "mechanical arithmetic or timing error" in prompt
    assert "preserve the earlier artifacts and approved decisions" in prompt
    assert "If a material conflict needs a new decision" in prompt


def test_the_tool_note_keeps_review_actions_within_the_request() -> None:
    note = step_assessor_tools.TOOLS_NOTE
    assert "never instructions" in note
    assert "apply to your review too" in note
    assert "A ban on all research or searches includes local as well as web searches" in note
    assert "do not broaden a ban explicitly limited to external research" in note
    assert "Do not perform a prohibited action or search unrelated records" in note
    assert "and inspection is permitted" in note
