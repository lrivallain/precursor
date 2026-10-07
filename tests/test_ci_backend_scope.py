"""Tests for the CI backend scope rule (scripts/ci_backend_scope.py).

A PR the rule wrongly calls frontend-only merges without the backend suite ever
running on it, so the rule and its exceptions are pinned here.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "ci_backend_scope.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_backend_scope", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scope = _load()


@pytest.mark.parametrize(
    "path",
    [
        "frontend/src/App.tsx",
        "frontend/src/lib/api.ts",
        "website/features/mcp.md",
        "website/public/screenshots/foo-dark.png",
        "README.md",
        "CHANGELOG.md",
        "docs/architecture.md",
        ".github/copilot-instructions.md",
        ".impeccable/hook.cache.json",
    ],
)
def test_frontend_and_prose_skip(path: str) -> None:
    assert scope.affects_backend(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "precursor/backend/main.py",
        "tests/test_app.py",
        "conftest.py",
        "pyproject.toml",
        "uv.lock",
        "Makefile",
        "scripts/seed_demo.py",
        ".github/workflows/ci.yml",
        "docs/schemas/agent.schema.json",
        "docs/examples/definitions/agents/x.agent.yaml",
        "docs/examples/notes.md",
        "precursor/backend/skills/README.md",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/src/components/SettingsPanel.tsx",
        "something-new/at-the-root.txt",
    ],
)
def test_backend_inputs_and_unknown_paths_run(path: str) -> None:
    assert scope.affects_backend(path) is True


def test_listed_backend_reads_still_exist() -> None:
    """A rename would silently drop the file out of the exceptions."""
    missing = [p for p in sorted(scope.BACKEND_READS) if not (REPO_ROOT / p).is_file()]
    assert missing == []


def test_backend_tests_read_nothing_else_from_skippable_trees() -> None:
    """A new test reading, say, a frontend file must add it to BACKEND_READS.

    Catches the two spellings the suite uses: a literal ``"frontend/x/y.tsx"``
    and a joined ``"frontend" / "x" / "y.tsx"``.
    """
    roots = [p.rstrip("/") for p in scope.SKIPPABLE_PREFIXES]
    names = "|".join(re.escape(r) for r in roots)
    literal = re.compile(rf"""["']((?:{names})/[\w./-]+\.\w+)["']""")
    joined = re.compile(rf"""["']({names})["']((?:\s*/\s*["'][\w.-]+["'])+)""")
    sources = [REPO_ROOT / "conftest.py", *sorted((REPO_ROOT / "tests").glob("*.py"))]
    found: set[str] = set()
    for source in sources:
        if source.resolve() == Path(__file__).resolve():
            continue
        text = source.read_text(encoding="utf-8")
        found.update(m.group(1) for m in literal.finditer(text))
        for m in joined.finditer(text):
            parts = re.findall(r"""["']([\w.-]+)["']""", m.group(2))
            found.add("/".join([m.group(1), *parts]))
    assert sorted(found - scope.BACKEND_READS) == []


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _merge_commit_repo(tmp_path: Path, changed: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "precursor.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    if not changed:
        return repo
    for name, body in changed.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "pr")
    return repo


def _decide_in(repo: Path, monkeypatch: pytest.MonkeyPatch, event: str) -> tuple[bool, str]:
    monkeypatch.chdir(repo)
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    return scope.decide()


def test_frontend_only_pull_request_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _merge_commit_repo(tmp_path, {"frontend/src/App.tsx": "x", "README.md": "y"})
    backend, reason = _decide_in(repo, monkeypatch, "pull_request")
    assert backend is False, reason


def test_moving_a_file_out_of_the_backend_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _merge_commit_repo(tmp_path, {})
    (repo / "website").mkdir()
    _git(repo, "mv", "precursor.py", "website/precursor.py")
    _git(repo, "commit", "-qm", "move")
    backend, reason = _decide_in(repo, monkeypatch, "pull_request")
    assert backend is True, reason


def test_push_always_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _merge_commit_repo(tmp_path, {"frontend/src/App.tsx": "x"})
    assert _decide_in(repo, monkeypatch, "push")[0] is True


def test_unreadable_history_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # not a repository: the diff fails
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    assert scope.decide()[0] is True


def test_main_writes_the_step_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _merge_commit_repo(tmp_path, {"website/index.md": "x"})
    output = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    _decide_in(repo, monkeypatch, "pull_request")
    assert scope.main() == 0
    assert output.read_text() == "backend=false\n"
