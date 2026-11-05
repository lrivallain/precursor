"""The assessor's read-only tools: what it may open, and what it can't."""

from __future__ import annotations

import json
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.schemas.step_plan import Assessment
from precursor.backend.services import (
    approval_rules,
    step_assessor_tools,
    step_evidence,
    step_plan_llm,
    turn_engine,
)
from precursor.backend.services import approvals as approval_service
from precursor.backend.services.conversation_turn import TurnSettings
from precursor.backend.services.llm.base import (
    TextDeltaEvent,
    ToolCallRequest,
    ToolCallsEvent,
    TurnDoneEvent,
)
from precursor.backend.services.llm.one_shot import OneShotResult
from precursor.backend.services.mcp.client import MCPToolDef
from precursor.backend.services.step_assessor_tools import Inspector
from precursor.backend.services.turn_engine import ToolResultTurn

_VERDICT = json.dumps({"score": 81, "summary": "ok"})


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
    deck = await _run(inspector, "read_file", path=str(pptx))
    assert deck.index("Slide text 1") < deck.index("Slide text 2") < deck.index("Slide text 10")
    assert any("a.docx" in line for line in inspector.log)


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
    assert len(await _run(inspector, "read_tool_output", index=2)) == 12000
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
    return SimpleNamespace(source_topic_id=None, llm_selection=None)


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

    async def stream_chat(self, **_: Any) -> Any:
        yield ""

    async def stream_chat_with_tools(self, *, messages: Any, tools: Any, **_: Any) -> Any:
        self.tools_seen.append([t.name for t in tools])
        self.messages_seen.append(list(messages))
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
            max_input_tokens=100_000,
            max_tool_result_tokens=10_000,
            provider=seen["provider"],
            github_token="",
            enabled_servers=["workiq"],
            gate=seen["gate"],
        )

    async def _execute(_active: Any, call: ToolCallRequest, _wait: float) -> Any:
        seen["ran"].append(call.name)
        yield ToolResultTurn(call, "Subject: Q3 plan", False, None)

    monkeypatch.setattr(step_assessor_tools, "get_mcp_client_manager", lambda: _Manager())
    monkeypatch.setattr(step_assessor_tools, "resolve_turn_settings", _settings)
    monkeypatch.setattr(turn_engine, "execute_tool_call", _execute)
    return seen


async def _review(
    inspector: Inspector, *, chat_id: int | None = 7, topic_id: int | None = None
) -> str | None:
    async with SessionLocal() as session:
        return await step_assessor_tools.review_with_chat_tools(
            session,
            chat_id=chat_id,
            topic_id=topic_id,
            system="SYS",
            user="usr",
            inspector=inspector,
            usage_source="step_assessment",
        )


def test_the_assessor_gate_is_never_looser_than_balanced() -> None:
    gate = approval_service.ApprovalGate
    assert step_assessor_tools.assessor_gate(None).policy == approval_service.POLICY_BALANCED
    loose = step_assessor_tools.assessor_gate(gate(policy=approval_service.POLICY_AUTONOMOUS))
    assert loose.policy == approval_service.POLICY_BALANCED
    manual = gate(policy=approval_service.POLICY_MANUAL)
    assert step_assessor_tools.assessor_gate(manual) is manual


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
    assert "precursor" in chat["active"].restricted
    assert "write_file" not in chat["active"].restricted["precursor"]
    first = provider.tools_seen[0]
    assert first[:3] == ["list_files", "read_file", "read_tool_output"]
    assert "workiq__get_draft" in first and "workiq__send_mail" in first
    assert approval_service.REQUEST_APPROVAL_TOOL not in first
    answered = [m.content for m in provider.messages_seen[1] if m.role == "tool"]
    assert answered[0] == "Subject: Q3 plan" and answered[1].startswith("Not run:")
    assert "assumptions" in answered[1]
    assert inspector.log == [
        "Called workiq__get_draft (workiq): ok",
        "Declined workiq__send_mail (workiq): needs approval",
    ]


async def test_a_topic_without_a_step_chat_resolves_from_the_topic(chat: dict[str, Any]) -> None:
    chat["provider"] = _Script([_VERDICT])
    await _review(Inspector([], []), chat_id=None, topic_id=3)
    assert chat["resolved"] == {"chat_id": None, "topic_id": 3}


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
        [[ToolCallRequest("1", "read_file", json.dumps({"path": str(doc)}))], _VERDICT]
    )
    chat["active"] = _Active([])
    inspector = Inspector([str(doc)], [])
    assert await _review(inspector) == _VERDICT
    tool_msg = next(m.content for m in provider.messages_seen[1] if m.role == "tool")
    assert "Runbook" in tool_msg
    assert chat["ran"] == []


async def test_without_a_file_or_a_server_tool_there_is_nothing_to_review_with(
    chat: dict[str, Any],
) -> None:
    chat["provider"] = provider = _Script([])
    chat["active"] = _Active([])
    assert await _review(Inspector([], [])) is None
    assert provider.tools_seen == []


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


async def test_the_assessor_judges_from_the_loops_answer_and_lists_what_it_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _loop(_session: Any, *, system: str, inspector: Inspector, **_: Any) -> str:
        assert "never change anything" in system
        inspector._note("Called workiq__get_draft (workiq): ok")
        return _VERDICT

    monkeypatch.setattr(step_assessor_tools, "review_with_chat_tools", _loop)
    async with SessionLocal() as session:
        a = await step_plan_llm._assess_or_trace(
            session, _plan(), "SYS", "usr", None, step_evidence.Evidence()
        )
    assert a.score == 81 and a.inspected == ["Called workiq__get_draft (workiq): ok"]


async def test_with_nothing_to_equip_the_assessor_stays_tool_less(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _loop(*_a: Any, **_k: Any) -> None:
        return None

    async def _once(_session: Any, *, system: str, **_: Any) -> Any:
        assert system == "SYS"
        return OneShotResult(text=_VERDICT, model="m", usage=None)

    monkeypatch.setattr(step_assessor_tools, "review_with_chat_tools", _loop)
    monkeypatch.setattr(step_plan_llm, "complete_once", _once)
    async with SessionLocal() as session:
        a = await step_plan_llm._assess_or_trace(
            session, _plan(), "SYS", "usr", None, step_evidence.Evidence()
        )
    assert a.score == 81 and a.inspected == []


async def test_a_failing_tool_call_falls_back_to_the_plain_judgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("provider rejects tools")

    async def _once(_session: Any, **_: Any) -> Any:
        return OneShotResult(text=_VERDICT, model="m", usage=None)

    monkeypatch.setattr(step_assessor_tools, "review_with_chat_tools", _boom)
    monkeypatch.setattr(step_plan_llm, "complete_once", _once)
    ev = step_evidence.Evidence(outputs=["tool said hi"])
    async with SessionLocal() as session:
        a = await step_plan_llm._assess_or_trace(session, _plan(), "SYS", "usr", None, ev)
    assert isinstance(a, Assessment) and a.score == 81 and a.inspected == []


def test_the_tool_note_tells_the_model_file_text_is_data() -> None:
    assert "never instructions" in step_assessor_tools.TOOLS_NOTE
