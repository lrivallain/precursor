"""The assessor's read-only tools: what it may open, and what it can't."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from precursor.backend.schemas.step_plan import Assessment
from precursor.backend.services import step_assessor_tools, step_evidence, step_plan_llm
from precursor.backend.services.llm.base import ToolCallRequest
from precursor.backend.services.llm.one_shot import OneShotResult
from precursor.backend.services.step_assessor_tools import Inspector

_VERDICT = json.dumps({"score": 81, "summary": "ok"})


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
    a = await step_plan_llm._assess_or_trace(None, _plan(), "SYS", "usr", None, ev)  # type: ignore[arg-type]
    assert a.score == 81 and "Runbook" in seen["file"]
    assert seen["tools"] == ["list_files", "read_file", "read_tool_output"]
    assert "read-only tools" in seen["system"]
    assert a.inspected and "runbook.docx" in a.inspected[0]


async def test_without_a_file_the_assessor_stays_tool_less(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("no tools expected")

    async def _once(_session: Any, *, system: str, **_: Any) -> Any:
        assert system == "SYS"
        return OneShotResult(text=_VERDICT, model="m", usage=None)

    monkeypatch.setattr(step_plan_llm, "complete_with_tools", _boom)
    monkeypatch.setattr(step_plan_llm, "complete_once", _once)
    a = await step_plan_llm._assess_or_trace(  # type: ignore[arg-type]
        None, _plan(), "SYS", "usr", None, step_evidence.Evidence()
    )
    assert a.score == 81 and a.inspected == []


async def test_a_failing_tool_call_falls_back_to_the_plain_judgement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("provider rejects tools")

    async def _once(_session: Any, **_: Any) -> Any:
        return OneShotResult(text=_VERDICT, model="m", usage=None)

    monkeypatch.setattr(step_plan_llm, "complete_with_tools", _boom)
    monkeypatch.setattr(step_plan_llm, "complete_once", _once)
    ev = step_evidence.Evidence(outputs=["tool said hi"])
    a = await step_plan_llm._assess_or_trace(None, _plan(), "SYS", "usr", None, ev)  # type: ignore[arg-type]
    assert isinstance(a, Assessment) and a.score == 81 and a.inspected == []


def test_the_tool_note_tells_the_model_file_text_is_data() -> None:
    assert "never instructions" in step_assessor_tools.TOOLS_NOTE
