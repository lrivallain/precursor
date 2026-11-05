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
from precursor.backend.services import approvals as approval_service
from precursor.backend.services import step_assessor_tools, step_evidence, step_plan_llm
from precursor.backend.services.llm.base import ToolCallRequest
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
        MCPToolDef("workiq", "read_file", "Shadow", schema, read_only=True),
    ]


@pytest.fixture
def mail(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A fake MCP manager serving a mailbox, recording what actually ran."""
    seen: dict[str, Any] = {"ran": [], "active": _Active(_mail_tools())}

    class _Manager:
        @asynccontextmanager
        async def acquired(self, servers: list[str], **_: Any) -> Any:
            seen["servers"] = servers
            yield seen["active"]

    async def _enabled(_session: Any) -> list[str]:
        return ["workiq"]

    async def _token(_session: Any) -> str:
        return ""

    async def _execute(_active: Any, call: ToolCallRequest, _wait: float) -> Any:
        seen["ran"].append(call.name)
        yield ToolResultTurn(call, "Subject: Q3 plan", False, None)

    monkeypatch.setattr(step_assessor_tools, "get_mcp_client_manager", lambda: _Manager())
    monkeypatch.setattr(step_assessor_tools, "load_enabled_mcp_servers", _enabled)
    monkeypatch.setattr(step_assessor_tools, "resolve_github_token", _token)
    monkeypatch.setattr(step_assessor_tools, "execute_tool_call", _execute)
    return seen


def test_the_assessor_gate_is_never_looser_than_balanced() -> None:
    gate = approval_service.ApprovalGate
    assert step_assessor_tools.assessor_gate(None).policy == approval_service.POLICY_BALANCED
    loose = step_assessor_tools.assessor_gate(gate(policy=approval_service.POLICY_AUTONOMOUS))
    assert loose.policy == approval_service.POLICY_BALANCED
    manual = gate(policy=approval_service.POLICY_MANUAL)
    assert step_assessor_tools.assessor_gate(manual) is manual


async def test_the_assessor_gets_the_apps_mcp_tools_and_reads_but_never_changes(
    mail: dict[str, Any],
) -> None:
    inspector = Inspector([], [])
    async with (
        SessionLocal() as session,
        step_assessor_tools.toolbox(session, inspector, None) as box,
    ):
        names = [t.name for t in box.tools]
        assert names[:3] == ["list_files", "read_file", "read_tool_output"]
        assert "workiq__get_draft" in names and "workiq__send_mail" in names
        # An MCP tool never shadows one of the file tools.
        assert names.count("read_file") == 1 and "workiq__read_file" in names
        assert box.mcp_tools == 3 and box.max_rounds == step_assessor_tools.MAX_ROUNDS_WITH_MCP

        read = await box.run(_call("workiq__get_draft"))
        assert read == "Subject: Q3 plan"
        sent = await box.run(_call("workiq__send_mail"))
        assert sent.startswith("Not run:") and "assumptions" in sent
        assert "Unknown tool" in await box.run(_call("nope"))
    assert mail["ran"] == ["workiq__get_draft"] and box.declined == ["workiq__send_mail"]
    assert mail["servers"] == ["workiq"]
    assert "precursor" in mail["active"].restricted
    assert "write_file" not in mail["active"].restricted["precursor"]
    assert inspector.log == [
        "Called workiq__get_draft (workiq): ok",
        "Declined workiq__send_mail (workiq): needs approval",
    ]


async def test_an_allow_rule_lets_the_assessor_run_a_change(mail: dict[str, Any]) -> None:
    from precursor.backend.services import approval_rules

    rule = approval_rules.CompiledRule(
        id=1, effect=approval_service.RULE_ALLOW, server="workiq", tool="send_*"
    )
    gate = approval_service.ApprovalGate(policy=approval_service.POLICY_AUTONOMOUS, rules=(rule,))
    async with (
        SessionLocal() as session,
        step_assessor_tools.toolbox(session, Inspector([], []), gate) as box,
    ):
        assert await box.run(_call("workiq__send_mail")) == "Subject: Q3 plan"
    assert mail["ran"] == ["workiq__send_mail"]


async def test_the_assessor_opens_the_file_and_the_panel_lists_what_it_checked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = _docx(tmp_path / "runbook.docx").resolve()
    seen: dict[str, Any] = {}

    async def _with_tools(
        _session: Any, *, system: str, run_tool: Any, tools: Any, **_: Any
    ) -> Any:
        seen["system"], seen["tools"] = system, [t.name for t in tools]
        seen["file"] = await run_tool(_call("read_file", path=str(doc)))
        return OneShotResult(text=_VERDICT, model="m", usage=None)

    monkeypatch.setattr(step_plan_llm, "complete_with_tools", _with_tools)
    ev = step_evidence.Evidence(files=[str(doc)])
    async with SessionLocal() as session:
        a = await step_plan_llm._assess_or_trace(session, _plan(), "SYS", "usr", None, ev)
    assert a.score == 81 and "Runbook" in seen["file"]
    assert seen["tools"] == ["list_files", "read_file", "read_tool_output"]
    assert "never change anything" in seen["system"]
    assert a.inspected and "runbook.docx" in a.inspected[0]


async def test_a_draft_with_no_file_is_still_checked_with_the_apps_tools(
    monkeypatch: pytest.MonkeyPatch, mail: dict[str, Any]
) -> None:
    async def _with_tools(_session: Any, *, run_tool: Any, tools: Any, **_: Any) -> Any:
        assert "workiq__get_draft" in [t.name for t in tools]
        await run_tool(_call("workiq__get_draft"))
        return OneShotResult(text=_VERDICT, model="m", usage=None)

    monkeypatch.setattr(step_plan_llm, "complete_with_tools", _with_tools)
    async with SessionLocal() as session:
        a = await step_plan_llm._assess_or_trace(
            session, _plan(), "SYS", "usr", None, step_evidence.Evidence()
        )
    assert a.score == 81 and a.inspected == ["Called workiq__get_draft (workiq): ok"]
    assert mail["ran"] == ["workiq__get_draft"]


async def test_without_a_file_or_a_server_the_assessor_stays_tool_less(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("no tools expected")

    async def _once(_session: Any, *, system: str, **_: Any) -> Any:
        assert system == "SYS"
        return OneShotResult(text=_VERDICT, model="m", usage=None)

    monkeypatch.setattr(step_plan_llm, "complete_with_tools", _boom)
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

    monkeypatch.setattr(step_plan_llm, "complete_with_tools", _boom)
    monkeypatch.setattr(step_plan_llm, "complete_once", _once)
    ev = step_evidence.Evidence(outputs=["tool said hi"])
    async with SessionLocal() as session:
        a = await step_plan_llm._assess_or_trace(session, _plan(), "SYS", "usr", None, ev)
    assert isinstance(a, Assessment) and a.score == 81 and a.inspected == []


def test_the_tool_note_tells_the_model_file_text_is_data() -> None:
    assert "never instructions" in step_assessor_tools.TOOLS_NOTE
