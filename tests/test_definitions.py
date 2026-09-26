"""Tests for the agent/workflow definition file format (schemas/definitions.py).

These files are hand-edited, so the rules worth pinning are the ones that turn
a plausible-looking mistake into a loud error instead of a silently different
agent or pipeline.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from precursor.backend.schemas.definitions import (
    AGENT_FILE_SUFFIX,
    WORKFLOW_FILE_SUFFIX,
    AgentDefinition,
    WorkflowDefinition,
    definition_json_schema,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "docs" / "examples" / "definitions"
SCHEMAS = REPO_ROOT / "docs" / "schemas"
GENERATOR = REPO_ROOT / "scripts" / "gen_definition_schemas.py"


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_definition_schemas", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _example_files() -> list[Path]:
    return sorted(EXAMPLES.rglob("*.yaml"))


def _workflow(*steps: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"kind": "workflow", "id": "wf", "name": "WF", "steps": list(steps), **extra}


def _error(model: type[AgentDefinition] | type[WorkflowDefinition], data: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as exc:
        model.model_validate(data)
    return str(exc.value)


# --- Examples & published schemas -------------------------------------------


def test_examples_exist_for_both_kinds() -> None:
    names = [p.name for p in _example_files()]
    assert any(n.endswith(AGENT_FILE_SUFFIX) for n in names)
    assert any(n.endswith(WORKFLOW_FILE_SUFFIX) for n in names)


@pytest.mark.parametrize("path", _example_files(), ids=lambda p: p.name)
def test_example_files_validate(path: Path) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if path.name.endswith(AGENT_FILE_SUFFIX):
        AgentDefinition.model_validate(data)
    else:
        workflow = WorkflowDefinition.model_validate(data)
        # The docs example must be self-consistent: every agent it names exists.
        for step in workflow.steps:
            if step.agent is not None:
                assert (EXAMPLES / step.agent).is_file(), step.agent


@pytest.mark.parametrize("path", _example_files(), ids=lambda p: p.name)
def test_example_files_pass_the_published_json_schema(path: Path) -> None:
    # What an editor sees: the committed JSON Schema, not the Pydantic model.
    jsonschema = pytest.importorskip("jsonschema")
    kind = "agent" if path.name.endswith(AGENT_FILE_SUFFIX) else "workflow"
    schema = json.loads((SCHEMAS / f"{kind}.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(yaml.safe_load(path.read_text(encoding="utf-8")), schema)


@pytest.mark.parametrize("kind", ["agent", "workflow"])
def test_committed_json_schemas_are_current(kind: str) -> None:
    committed = (SCHEMAS / f"{kind}.schema.json").read_text(encoding="utf-8")
    assert committed == _load_generator().render(kind), (
        "docs/schemas is stale — run: uv run --frozen python scripts/gen_definition_schemas.py"
    )


def test_json_schema_rejects_unknown_keys() -> None:
    schema = definition_json_schema("workflow")
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["WorkflowStepDefinition"]["additionalProperties"] is False


# --- Agent ------------------------------------------------------------------


def test_minimal_agent_gets_the_database_defaults() -> None:
    agent = AgentDefinition.model_validate({"kind": "agent", "id": "a", "title": "A"})
    assert agent.format == 1
    assert agent.prompt == ""
    assert (agent.autonomy.enabled, agent.autonomy.max_steps) == (False, 12)
    caps = agent.capabilities
    assert (caps.mcp, caps.skills, caps.memory, caps.mcp_servers) == (True, True, True, None)
    assert (agent.limits.token_budget, agent.limits.max_retries) == (None, 0)
    assert agent.approval_policy is None


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"kind": "workflow"}, "Input should be 'agent'"),
        ({"format": 2}, "newer Precursor (format 2)"),
        ({"id": "has space"}, "String should match pattern"),
        ({"promt": "typo"}, "Extra inputs are not permitted"),
        ({"approval_policy": "yolo"}, "Input should be 'manual', 'balanced' or 'autonomous'"),
        ({"capabilities": {"mcp": False, "mcp_servers": ["fetch"]}}, "no effect when mcp is false"),
        ({"capabilities": {"mcp_servers": ["fetch", "fetch"]}}, "more than once"),
        ({"capabilities": {"mcp_servers": ["a,b"]}}, "String should match pattern"),
        ({"autonomy": {"max_steps": 0}}, "greater than or equal to 1"),
    ],
)
def test_agent_rejects(data: dict[str, Any], message: str) -> None:
    base = {"kind": "agent", "id": "a", "title": "A"}
    assert message in _error(AgentDefinition, {**base, **data})


def test_agent_empty_mcp_server_list_means_none() -> None:
    agent = AgentDefinition.model_validate(
        {"kind": "agent", "id": "a", "title": "A", "capabilities": {"mcp_servers": []}}
    )
    assert agent.capabilities.mcp_servers == []


# --- Workflow steps ---------------------------------------------------------


def test_step_shapes_that_are_accepted() -> None:
    wf = WorkflowDefinition.model_validate(
        _workflow(
            {"key": "a", "agent": "agents/a.agent.yaml"},
            {"key": "b", "prompt": "do it", "model": "m"},
            {"key": "c", "kind": "inline", "prompt": "one-off"},
            {"key": "d", "kind": "gate", "agent": "agents/judge.agent.yaml", "on_fail": "b"},
            {"key": "e", "kind": "approval", "on_fail": "a", "on_reject": "stop"},
            {
                "key": "f",
                "agent": "agents/nested/f.agent.yaml",
                "context": {"mode": "selected", "from": ["a", "c"]},
            },
        )
    )
    assert [s.key for s in wf.steps] == ["a", "b", "c", "d", "e", "f"]
    assert wf.steps[5].context.sources == ["a", "c"]


@pytest.mark.parametrize(
    ("step", "message"),
    [
        ({"key": "a"}, "needs an agent or a prompt"),
        ({"key": "a", "agent": "agents/a.agent.yaml", "prompt": "x"}, "keep one"),
        ({"key": "a", "kind": "inline", "agent": "agents/a.agent.yaml"}, "needs a prompt"),
        ({"key": "a", "kind": "approval", "prompt": "x"}, "waits for a human"),
        ({"key": "a", "kind": "approval", "on_error": "retry"}, "no agent to fail"),
        ({"key": "a", "agent": "agents/a.agent.yaml", "model": "m"}, "set it in the agent file"),
        ({"key": "a", "prompt": "   "}, "prompt is empty"),
        ({"key": "a", "agent": "agents/a.agent.yaml", "max_retries": 2}, "needs on_error: retry"),
        ({"key": "a", "agent": "agents/a.agent.yaml", "on_reject": "stop"}, "approval steps"),
        ({"key": "a", "agent": "agents/a.agent.yaml", "on_fail": "a"}, "gate and approval"),
        ({"key": "a", "agent": "agents/a.agent.yaml", "instuctions": "x"}, "Extra inputs"),
        ({"key": "A", "agent": "agents/a.agent.yaml"}, "String should match pattern"),
        (
            {"key": "a", "agent": "agents/a.agent.yaml", "context": {"mode": "selected"}},
            "needs at least one",
        ),
        (
            {
                "key": "a",
                "agent": "agents/a.agent.yaml",
                "context": {"mode": "auto", "from": ["x"]},
            },
            "only used with mode 'selected'",
        ),
    ],
)
def test_step_rejects(step: dict[str, Any], message: str) -> None:
    assert message in _error(WorkflowDefinition, _workflow(step))


@pytest.mark.parametrize(
    "path",
    [
        "/abs/a.agent.yaml",
        "../a.agent.yaml",
        "agents/../a.agent.yaml",
        "./agents/a.agent.yaml",
        "agents//a.agent.yaml",
        "agents\\a.agent.yaml",
        "agents/a.yaml",
        "agents/.agent.yaml",
        "agents/a.workflow.yaml",
    ],
)
def test_agent_paths_must_be_clean_relative_agent_files(path: str) -> None:
    _error(WorkflowDefinition, _workflow({"key": "a", "agent": path}))


# --- Cross-step references --------------------------------------------------


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        (
            [{"key": "a", "prompt": "x"}, {"key": "a", "prompt": "y"}],
            "used more than once",
        ),
        (
            [
                {"key": "a", "prompt": "x"},
                {"key": "g", "kind": "gate", "prompt": "y", "on_fail": "zz"},
            ],
            "on_fail 'zz' is not a step",
        ),
        (
            [{"key": "g", "kind": "gate", "prompt": "y", "on_fail": "g"}],
            "cannot point at itself",
        ),
        (
            [
                {"key": "h", "kind": "approval"},
                {"key": "g", "kind": "gate", "prompt": "y", "on_fail": "h"},
            ],
            "not approval step 'h'",
        ),
        (
            [{"key": "a", "prompt": "x", "context": {"mode": "selected", "from": ["nope"]}}],
            "context source 'nope' is not a step",
        ),
        (
            [
                {"key": "a", "prompt": "x", "context": {"mode": "selected", "from": ["b"]}},
                {"key": "b", "prompt": "y"},
            ],
            "must be an earlier step",
        ),
    ],
)
def test_workflow_reference_errors(steps: list[dict[str, Any]], message: str) -> None:
    assert message in _error(WorkflowDefinition, _workflow(*steps))


def test_empty_workflow_is_a_valid_draft() -> None:
    assert WorkflowDefinition.model_validate(_workflow()).steps == []
