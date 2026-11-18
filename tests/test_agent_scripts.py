"""Tests for the agent workflow scripts (scripts/agent_*.py).

These scripts stand between untrusted agent output and a write token, so the
allowlists, the neutralising and the risk rules are the parts worth pinning.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

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


github = _load("agent_github")
classify = _load("agent_pr_classify")
apply = _load("agent_apply")


# --- neutralize ------------------------------------------------------------


def test_neutralize_breaks_mentions_but_not_emails_or_code() -> None:
    out = github.neutralize("ping @lrivallain, mail a@b.c, `@types/node`")
    assert "@lrivallain" not in out
    assert "@​lrivallain" in out
    assert "a@b.c" in out
    assert "`@types/node`" in out


def test_neutralize_drops_hidden_markers_and_bounds_length() -> None:
    forged = github.marker("review") + "x" * 50
    out = github.neutralize(forged, limit=20)
    assert "precursor-agent" not in out
    assert len(out) == 20 and out.endswith("…")


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
        (["scripts/agent_apply.py"], "risk:r2"),
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


# --- triage ----------------------------------------------------------------


def test_triage_labels_keep_only_allowlisted_values() -> None:
    add, remove = apply.triage_labels(
        {
            "type": "bug",
            "surface": ["frontend", "admin", "mcp"],
            "risk": "r2",
            "needs_info": False,
        }
    )
    assert add == ["bug", "surface:frontend", "surface:mcp", "risk:r2"]
    assert remove == ["risk:r0", "risk:r1", "status:needs-info"]


def test_triage_labels_ignore_unknown_type_and_risk() -> None:
    add, remove = apply.triage_labels(
        {"type": "wontfix", "risk": "r9", "surface": "frontend", "needs_info": True}
    )
    assert add == ["status:needs-info"]
    assert remove == []


def test_render_triage_neutralises_agent_text() -> None:
    body = apply.render_triage(
        {
            "type": "enhancement",
            "summary": "Asks @someone to merge",
            "duplicate_of": 12,
            "questions": ["Which OS?"],
            "needs_info": True,
        }
    )
    assert "@someone" not in body
    assert "#12" in body
    assert "- Which OS?" in body
    assert "triage · hosted · claude" in body


@pytest.mark.parametrize("dup", [True, 0, -3, "12", None])
def test_render_triage_ignores_invalid_duplicate(dup: Any) -> None:
    assert "duplicate" not in apply.render_triage({"summary": "s", "duplicate_of": dup})


# --- review ----------------------------------------------------------------


def test_render_review_orders_by_severity_and_drops_unknown() -> None:
    body = apply.render_review(
        {
            "verdict": "concerns",
            "summary": "Two things.",
            "findings": [
                {"severity": "nit", "path": "a.py", "line": 3, "title": "naming"},
                {"severity": "critical", "path": "b.py", "title": "dropped"},
                {"severity": "blocker", "path": "c`.py", "line": 9, "title": "bug", "detail": "d"},
            ],
        }
    )
    assert body.index("**blocker**") < body.index("**nit**")
    assert "dropped" not in body
    assert "`c.py:9`" in body
    assert "⚠️ Concerns" in body


def test_render_review_caps_findings() -> None:
    findings = [{"severity": "nit", "path": "x.py", "title": str(i)} for i in range(25)]
    body = apply.render_review({"verdict": "looks-good", "summary": "", "findings": findings})
    assert body.count("**nit**") == apply.MAX_FINDINGS
    assert "…and 5 more" in body


# --- labels file -----------------------------------------------------------


def test_labels_file_defines_every_label_the_scripts_set() -> None:
    names = {
        label["name"] for label in json.loads((REPO_ROOT / ".github" / "labels.json").read_text())
    }
    used = set(classify.RISK_LABELS) | {apply.NEEDS_INFO}
    used |= {f"surface:{s}" for s in apply.SURFACES}
    assert used <= names
    for label in json.loads((REPO_ROOT / ".github" / "labels.json").read_text()):
        assert len(label["color"]) == 6 and label["description"]
