"""Tests for the quality ratchet (scripts/quality_ratchet.py).

The ratchet is what keeps complexity debt from growing back, so its comparison
rules (worse fails, better is reported, strict demands a tight baseline) are
worth pinning down.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "quality_ratchet.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("quality_ratchet", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ratchet = _load()


@pytest.fixture
def fake_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, int]:
    """Replace the real checks with one whose numbers the test controls."""
    current: dict[str, int] = {}
    monkeypatch.setattr(ratchet, "CHECKS", {"fake": lambda: dict(current)})
    monkeypatch.setattr(ratchet, "BASELINE_PATH", tmp_path / "baseline.json")
    return current


def test_compare_flags_growth_and_new_entries() -> None:
    regressions, improvements = ratchet.compare({"a": 5}, {"a": 6, "b": 1})
    assert regressions == ["a: 5 -> 6", "b: 0 -> 1"]
    assert improvements == []


def test_compare_reports_shrinking_and_removed_entries() -> None:
    regressions, improvements = ratchet.compare({"a": 5, "b": 2}, {"a": 3})
    assert regressions == []
    assert improvements == ["a: 5 -> 3", "b: 2 -> 0"]


def test_update_then_check_passes(fake_check: dict[str, int]) -> None:
    fake_check["x"] = 3
    assert ratchet.main(["--update"]) == 0
    assert json.loads(ratchet.BASELINE_PATH.read_text()) == {"fake": {"x": 3}}
    assert ratchet.main(["--strict"]) == 0


def test_regression_fails(fake_check: dict[str, int]) -> None:
    fake_check["x"] = 3
    ratchet.main(["--update"])
    fake_check["x"] = 4
    assert ratchet.main([]) == 1


def test_improvement_passes_but_strict_fails(fake_check: dict[str, int]) -> None:
    fake_check["x"] = 3
    ratchet.main(["--update"])
    fake_check["x"] = 2
    assert ratchet.main([]) == 0
    assert ratchet.main(["--strict"]) == 1


def test_missing_tool_skips_unless_required(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing() -> dict[str, int]:
        raise ratchet.ToolMissingError("nope")

    monkeypatch.setattr(ratchet, "CHECKS", {"tool": missing})
    assert ratchet.main([]) == 0
    assert ratchet.main(["--require-tools"]) == 1


def test_file_size_flags_only_files_over_the_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "small.py").write_text("x\n" * 10)
    (tmp_path / "big.tsx").write_text("x\n" * (ratchet.MAX_FILE_LINES + 1))
    monkeypatch.setattr(ratchet, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ratchet, "_source_files", lambda: ["small.py", "big.tsx"])
    assert ratchet.check_file_size() == {"big.tsx": ratchet.MAX_FILE_LINES + 1}


def test_untyped_responses_flags_only_shapeless_json() -> None:
    def op(schema: object) -> dict[str, object]:
        content = {"application/json": {"schema": schema}}
        return {"responses": {"200": {"content": content}}}

    openapi = {
        "paths": {
            "/typed": {"get": op({"$ref": "#/components/schemas/Topic"})},
            "/list": {"get": op({"type": "array", "items": {"type": "string"}})},
            "/dict": {"get": op({"type": "object", "additionalProperties": True})},
            "/any": {"post": op({})},
            "/dicts": {"get": op({"type": "array", "items": {"type": "object"}})},
            "/maybe": {"get": op({"anyOf": [{"type": "object"}, {"type": "null"}]})},
            "/map": {"get": op({"type": "object", "additionalProperties": {"type": "integer"}})},
            "/empty": {"delete": {"responses": {"204": {"description": "No Content"}}}},
        }
    }
    assert set(ratchet.untyped_responses(openapi)) == {
        "GET /dict",
        "POST /any",
        "GET /dicts",
        "GET /maybe",
    }


def test_committed_baseline_covers_every_check() -> None:
    baseline = json.loads((REPO_ROOT / "scripts" / "quality_baseline.json").read_text())
    assert set(baseline) == set(ratchet.CHECKS)
