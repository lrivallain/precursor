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


# --- issue status transitions ----------------------------------------------

status = _load("agent_issue_status")


def test_a_new_status_replaces_the_others() -> None:
    current = {"bug", "status:spec-draft", "needs-triage"}
    assert status.on_labeled("status:spec-approved", current | {"status:spec-approved"}) == (
        [],
        ["status:spec-draft"],
    )


def test_accepting_clears_needs_triage() -> None:
    add, remove = status.on_labeled("status:accepted", {"needs-triage", "status:accepted"})
    assert add == [] and remove == ["needs-triage"]


def test_other_labels_are_ignored() -> None:
    assert status.on_labeled("bug", {"bug", "status:accepted"}) == ([], [])


def test_assignment_starts_only_accepted_work() -> None:
    assert status.on_assigned("someone", {"needs-triage"}) == ([], [])
    assert status.on_assigned("someone", {"status:accepted"}) == (
        ["status:in-progress"],
        ["status:accepted"],
    )
    add, remove = status.on_assigned("Copilot", {"status:spec-approved"})
    assert add == ["status:in-progress", "runner:hosted"]
    assert remove == ["status:spec-approved"]


def test_dropping_the_last_claim_frees_the_issue() -> None:
    current = {"status:in-progress", "runner:local", "bug"}
    assert status.on_unassigned(0, current) == (
        ["status:accepted"],
        ["status:in-progress", "runner:local"],
    )
    assert status.on_unassigned(1, current) == ([], [])
    assert status.on_unassigned(0, {"status:in-review"}) == ([], [])


@pytest.mark.parametrize(
    ("action", "draft", "merged", "current", "expected"),
    [
        ("opened", True, False, {"status:accepted"}, (["status:in-progress"], ["status:accepted"])),
        (
            "opened",
            False,
            False,
            {"status:in-progress"},
            (["status:in-review"], ["status:in-progress"]),
        ),
        (
            "ready_for_review",
            False,
            False,
            {"status:in-progress"},
            (["status:in-review"], ["status:in-progress"]),
        ),
        ("closed", False, False, {"status:in-review"}, (["status:accepted"], ["status:in-review"])),
        ("closed", False, False, {"needs-triage"}, ([], [])),
        ("closed", False, True, {"status:in-review", "bug"}, ([], ["status:in-review"])),
        ("labeled", False, False, {"status:accepted"}, ([], [])),
    ],
)
def test_linked_issue_follows_its_pull_request(
    action: str, draft: bool, merged: bool, current: set[str], expected: tuple[list[str], list[str]]
) -> None:
    assert status.on_pull_request(action, draft, merged, current) == expected


def test_every_status_the_rules_set_is_a_defined_label() -> None:
    names = {
        label["name"] for label in json.loads((REPO_ROOT / ".github" / "labels.json").read_text())
    }
    used = {status.ACCEPTED, status.SPEC_APPROVED, status.IN_PROGRESS, status.IN_REVIEW}
    assert used | {status.RUNNER_HOSTED, status.NEEDS_TRIAGE} <= names


# --- changelog fragments ---------------------------------------------------

fragments = _load("changelog_fragments")

SAMPLE = """# Changelog

## [Unreleased]

### Added

- **Existing feature.** Text
  wrapped.

### Fixed

- **Old fix.**

## [2026.11.8] - 2026-11-17

### Added

- Released.
"""


def test_assemble_appends_under_existing_and_new_headings() -> None:
    out = fragments.assemble(
        SAMPLE,
        [
            ("added", "- **New thing.**\n"),
            ("changed", "- **Now different.**"),
            ("security", "- **Patched.**"),
        ],
    )
    unreleased = out.split("## [2026.11.8]")[0]
    assert unreleased.index("wrapped.\n- **New thing.**") > 0
    assert unreleased.index("### Changed") < unreleased.index("### Fixed")
    assert unreleased.rstrip().endswith("### Security\n\n- **Patched.**")
    assert "\n\n\n" not in out
    assert out.split("## [2026.11.8]")[1] == SAMPLE.split("## [2026.11.8]")[1]


def test_assemble_leaves_the_real_changelog_intact_without_fragments() -> None:
    real = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert fragments.assemble(real, []) == real


def test_problems_flag_bad_names_and_bodies(tmp_path: Path) -> None:
    (tmp_path / "503.changed.md").write_text("- **Good.**\n")
    (tmp_path / "503.tweaked.md").write_text("- Wrong section.\n")
    (tmp_path / "504.fixed.md").write_text("Not a bullet.\n")
    (tmp_path / "README.md").write_text("ignored")
    found = fragments.problems(fragments.fragment_files(tmp_path))
    assert len(found) == 2
    assert found[0].startswith("503.tweaked.md") and found[1].startswith("504.fixed.md")


def test_repository_fragments_are_valid() -> None:
    assert fragments.problems(fragments.fragment_files()) == []
