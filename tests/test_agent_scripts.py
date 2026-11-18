"""Tests for the agent workflow scripts (scripts/agent_*.py).

The classifier holds a write token on PRs from anyone (pull_request_target), so
its risk rules are the part worth pinning.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
# Built, not literal: these are classifier inputs, not files the tests read, and
# test_ci_backend_scope flags quoted paths into the SPA and website trees.
FE = "frontend"
WEB = "website"


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


classify = _load("agent_pr_classify")


# --- risk class ------------------------------------------------------------


@pytest.mark.parametrize(
    ("paths", "label"),
    [
        ([], "risk:r0"),
        ([f"{WEB}/features/mcp.md", "README.md", "tests/test_x.py"], "risk:r0"),
        ([f"{FE}/src/lib/foo.test.ts"], "risk:r0"),
        ([f"{FE}/src/lib/foo.ts", f"{WEB}/index.md"], "risk:r1"),
        (["precursor/backend/routers/chats.py"], "risk:r1"),
        ([f"{WEB}/index.md", ".github/workflows/ci.yml"], "risk:r2"),
        (["precursor/backend/alembic/versions/0042_x.py"], "risk:r2"),
        (["AGENTS.md"], "risk:r2"),
        (["CLAUDE.md"], "risk:r2"),
        ([".github/skills/review/SKILL.md"], "risk:r2"),
        (["scripts/agent_labels.py"], "risk:r2"),
        (["scripts/quality_baseline.json"], "risk:r1"),
        ([".claude/skills"], "risk:r2"),
        ([".claude/launch.json"], "risk:r1"),
        (["pyproject.toml"], "risk:r1"),
    ],
)
def test_risk_label_takes_the_highest_file(paths: list[str], label: str) -> None:
    assert classify.risk_label(paths) == label


def test_codeowners_covers_every_r2_rule() -> None:
    owners = (REPO_ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    patterns = {line.split()[0] for line in owners.splitlines() if line.startswith("/")}
    for prefix in classify.R2_PREFIXES:
        expected = "/scripts/agent_*" if prefix == "scripts/agent_" else f"/{prefix}"
        assert expected in patterns, prefix
    for path in classify.R2_FILES:
        assert f"/{path}" in patterns, path


def test_baseline_raises_flags_growth_and_new_entries_only() -> None:
    base = {"file-size": {"a.ts": 900, "b.ts": 850}, "ruff": {"x.py::C901": 1}}
    head = {"file-size": {"a.ts": 880, "b.ts": 851, "c.ts": 801}, "ruff": {}}
    assert classify.baseline_raises(base, head) == [
        "file-size: b.ts (850 → 851)",
        "file-size: c.ts (0 → 801)",
    ]
    assert classify.baseline_raises(base, {"file-size": {"a.ts": 10}}) == []


# --- docs impact -----------------------------------------------------------


def test_docs_hints_for_api_and_ui_changes_without_docs() -> None:
    hints = classify.docs_hints(
        ["precursor/backend/schemas/chat.py", f"{FE}/src/components/Chat.tsx"]
    )
    assert len(hints) == 2
    assert any("api.md" in h for h in hints)
    assert any(f"{WEB}/features/" in h for h in hints)


def test_docs_hints_silent_when_docs_change_or_code_is_internal() -> None:
    assert classify.docs_hints(["precursor/backend/schemas/chat.py", "CHANGELOG.md"]) == []
    assert classify.docs_hints(["precursor/backend/services/links.py"]) == []
    assert classify.docs_hints([f"{FE}/src/components/Chat.test.tsx"]) == []


# --- labels file -----------------------------------------------------------


def test_labels_file_defines_every_label_the_workflow_uses() -> None:
    labels = json.loads((REPO_ROOT / ".github" / "labels.json").read_text())
    names = {label["name"] for label in labels}
    assert set(classify.RISK_LABELS) <= names
    assert {"needs-triage", "status:needs-info", "status:accepted"} <= names
    for label in labels:
        assert len(label["color"]) == 6 and label["description"]


# --- model-free issue triage -----------------------------------------------

triage = _load("agent_issue_triage")

FORM_BODY = """### Problem

The panel flickers.

### Surface

Frontend (SPA)

### Logs

_No response_
"""


def test_surface_label_reads_the_issue_form_field() -> None:
    assert triage.surface_label(FORM_BODY) == "surface:frontend"
    assert triage.surface_label(FORM_BODY.replace("Frontend (SPA)", "Docs / DX")) == "surface:docs"
    assert triage.surface_label(FORM_BODY.replace("Frontend (SPA)", "Other / unsure")) is None
    assert triage.surface_label("Free text, no form.") is None


def test_form_field_treats_no_response_as_empty() -> None:
    assert triage.form_field(FORM_BODY, "Logs") is None
    assert triage.form_field(FORM_BODY, "Problem") == "The panel flickers."


def test_every_form_surface_option_is_mapped() -> None:
    templates = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
    for form in ("bug_report.yml", "feature_request.yml"):
        text = (templates / form).read_text(encoding="utf-8")
        block = text.split("id: surface", 1)[1].split("validations:", 1)[0]
        options = re.findall(r"^\s+- (.+)$", block, re.M)
        assert options, form
        for option in options:
            if option != "Other / unsure":
                assert option.lower() in triage.SURFACES, (form, option)


def test_duplicates_need_most_of_the_title_words() -> None:
    candidates = [
        {
            "number": 334,
            "title": "Refactor: split services/agents/manager.py (3.5k lines)",
            "state": "CLOSED",
        },
        {"number": 12, "title": "Agents tab layout", "state": "OPEN"},
        {
            "number": 480,
            "title": "refactor(agents): split workflow.py and manager.py",
            "state": "OPEN",
        },
    ]
    found = triage.duplicates("refactor(agents): split workflow.py and manager.py", 480, candidates)
    assert [i["number"] for i in found] == [334]


def test_duplicates_ignore_short_or_generic_titles() -> None:
    candidates = [{"number": 1, "title": "Add support for a new feature", "state": "OPEN"}]
    assert triage.duplicates("Add new feature support", 2, candidates) == []
    assert triage.duplicates("Crash", 2, [{"number": 1, "title": "Crash", "state": "OPEN"}]) == []


def test_triage_comment_lists_duplicates() -> None:
    body = triage.triage_comment([{"number": 334, "state": "CLOSED"}])
    assert "- #334 (closed)" in body
    assert "triage · hosted · deterministic" in body
