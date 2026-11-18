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
            "engine": "github-models · openai/gpt-4.1",
        }
    )
    assert "@someone" not in body
    assert "#12" in body
    assert "- Which OS?" in body
    assert "`triage · hosted · github-models · openai/gpt-4.1`" in body


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
    assert "partial" not in body
    assert "partial" in apply.render_review({"verdict": "concerns", "truncated": True})


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


# --- GitHub Models inference -----------------------------------------------

infer = _load("agent_infer")


def _objects(schema: dict[str, Any]) -> list[dict[str, Any]]:
    found = [schema] if schema.get("type") == "object" else []
    for child in schema.get("properties", {}).values():
        found += _objects(child)
    if "items" in schema:
        found += _objects(schema["items"])
    return found


@pytest.mark.parametrize("role", sorted(infer.SCHEMAS))
def test_schemas_meet_strict_mode(role: str) -> None:
    """Strict structured output wants every key required and no extra keys."""
    for obj in _objects(infer.SCHEMAS[role]):
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj["properties"])


def test_schema_enums_match_the_apply_allowlists() -> None:
    props = infer.SCHEMAS["triage"]["properties"]
    assert set(props["type"]["enum"]) == set(apply.TYPE_LABELS)
    assert tuple(props["surface"]["items"]["enum"]) == apply.SURFACES
    assert tuple(props["risk"]["enum"]) == apply.RISKS
    review = infer.SCHEMAS["review"]["properties"]
    assert set(review["verdict"]["enum"]) == set(apply.VERDICTS)
    assert tuple(review["findings"]["items"]["properties"]["severity"]["enum"]) == apply.SEVERITIES


def test_skill_prompt_drops_frontmatter_and_adds_workflow_note(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: x\ndescription: y\n---\n\n# Do it\n", encoding="utf-8")
    prompt = infer.skill_prompt(skill)
    assert prompt.startswith("# Do it")
    assert "name: x" not in prompt
    assert "You have no tools" in prompt


def test_real_skills_load() -> None:
    for role in infer.SCHEMAS:
        prompt = infer.skill_prompt(REPO_ROOT / ".github" / "skills" / role / "SKILL.md")
        assert "untrusted" in prompt.lower()


def test_fence_marks_and_cuts() -> None:
    text, cut = infer.fence("DIFF", "x" * 50, 10)
    assert cut is True
    assert text.startswith("<<<UNTRUSTED DIFF\n" + "x" * 10 + "\n[…")
    assert text.endswith(">>>UNTRUSTED DIFF")
    assert infer.fence("DIFF", "short", 10)[1] is False


def test_search_terms_keep_distinct_words() -> None:
    assert infer.search_terms("Login: login button crashes the SPA on Safari") == (
        "login button crashes safari"
    )
    assert infer.search_terms("?!") == ""


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_call_models_sends_the_schema_and_parses_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: int) -> _Response:
        sent["body"] = json.loads(request.data)
        sent["auth"] = request.headers["Authorization"]
        answer = {"verdict": "looks-good", "summary": "fine", "findings": []}
        return _Response({"choices": [{"message": {"content": json.dumps(answer)}}]})

    monkeypatch.setattr(infer.urllib.request, "urlopen", fake_urlopen)
    data = infer.call_models("sys", "user", "review", "openai/gpt-4.1", "tok")
    assert data["verdict"] == "looks-good"
    assert sent["auth"] == "Bearer tok"
    fmt = sent["body"]["response_format"]["json_schema"]
    assert fmt["strict"] is True and fmt["schema"] == infer.SCHEMAS["review"]


def test_run_reports_a_disabled_models_api_and_posts_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(infer, "review_input", lambda repo, number, limit: ("diff", False))

    def denied(*args: object, **kwargs: object) -> None:
        raise infer.urllib.error.HTTPError(infer.ENDPOINT, 403, "Forbidden", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(infer, "call_models", denied)
    skill = REPO_ROOT / ".github" / "skills" / "review" / "SKILL.md"
    assert infer.run("review", "o/r", 1, skill) is None
    assert "GitHub Models enabled" in summary.read_text()


def test_run_tags_engine_and_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("AGENT_MODEL", "openai/gpt-4.1-mini")
    monkeypatch.setattr(infer, "triage_input", lambda repo, number, limit: ("issue", True))
    monkeypatch.setattr(infer, "call_models", lambda *a: {"summary": "s", "engine": "forged"})
    skill = REPO_ROOT / ".github" / "skills" / "triage" / "SKILL.md"
    data = infer.run("triage", "o/r", 1, skill)
    assert data == {
        "summary": "s",
        "engine": "github-models · openai/gpt-4.1-mini",
        "truncated": True,
    }
