"""Declarative definitions — the on-disk file format.

A definition file declares what an agent or a workflow *is* (prompt, model,
role, capabilities, policies, steps). Everything that evolves while it runs —
status, progress, runs, token spend, scheduling, triggers, webhook tokens —
stays in the database, keyed to the file by its stable ``id``. See
``docs/definitions.md``.

The same folder also holds **summary templates** (``*.summary.yaml``): the
instructions a live session's recap is written from. They have no database
side at all.

The models are strict (unknown keys are rejected) because these files are
written by hand: a typo such as ``instuctions:`` must fail loudly rather than
silently drop the mandate. Checks that span several files — duplicate ids, an
``agent:`` path that points nowhere, unknown role or MCP server names — belong
to the folder-level checker, not to these per-document models.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.json_schema import GenerateJsonSchema

from precursor.backend.schemas.agent import AgentApprovalPolicy
from precursor.backend.schemas.definitions_api import DefinitionFileKind
from precursor.backend.schemas.workflow import (
    WorkflowStepContextMode,
    WorkflowStepErrorPolicy,
    WorkflowStepKind,
    WorkflowStepRejectPolicy,
)

DEFINITION_FORMAT_VERSION = 1

AGENT_FILE_SUFFIX = ".agent.yaml"
WORKFLOW_FILE_SUFFIX = ".workflow.yaml"
SUMMARY_FILE_SUFFIX = ".summary.yaml"
SUMMARY_NAME_MAX = 80

FILE_SUFFIXES: dict[DefinitionFileKind, str] = {
    "agent": AGENT_FILE_SUFFIX,
    "workflow": WORKFLOW_FILE_SUFFIX,
    "summary": SUMMARY_FILE_SUFFIX,
}

DefinitionId = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
        description=(
            "Stable identity linking this file to its run history. Never change it; "
            "renaming or moving the file keeps the history as long as the id stays."
        ),
    ),
]

StepKey = Annotated[
    str,
    Field(
        pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$",
        description="Stable step identifier, unique within the workflow.",
    ),
]

McpServerName = Annotated[
    str, Field(min_length=1, max_length=64, pattern=r"^[^,\s]([^,]*[^,\s])?$")
]

RoleName = Annotated[str, Field(min_length=1, max_length=64)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_format(value: Any) -> Any:
    if isinstance(value, int) and not isinstance(value, bool) and value > DEFINITION_FORMAT_VERSION:
        raise ValueError(
            f"written by a newer Precursor (format {value}); upgrade Precursor to read it"
        )
    return value


def _check_mcp_scope(mcp: bool | None, servers: list[str] | None) -> None:
    if servers is None:
        return
    if mcp is False and servers:
        raise ValueError("mcp_servers has no effect when mcp is false")
    if len(set(servers)) != len(servers):
        raise ValueError("mcp_servers lists a server more than once")


# --- Agent ------------------------------------------------------------------


class AgentAutonomy(_Strict):
    enabled: bool = Field(
        default=False,
        description="Keep working toward the prompt on its own after each turn.",
    )
    max_steps: int = Field(
        default=12, ge=1, le=100, description="Autonomous steps before handing back."
    )


class AgentCapabilities(_Strict):
    mcp: bool = True
    skills: bool = True
    memory: bool = True
    mcp_servers: list[McpServerName] | None = Field(
        default=None,
        description="Only these MCP servers. Omit for every enabled server; [] for none.",
    )

    @model_validator(mode="after")
    def _scope(self) -> AgentCapabilities:
        _check_mcp_scope(self.mcp, self.mcp_servers)
        return self


class AgentLimits(_Strict):
    token_budget: int | None = Field(
        default=None, ge=1, description="Cumulative tokens before the agent is parked."
    )
    max_retries: int = Field(default=0, ge=0, le=10, description="Automatic re-runs on failure.")


class AgentDefinition(_Strict):
    """``agents/<slug>.agent.yaml``."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"title": "Precursor agent definition"},
    )

    kind: Literal["agent"]
    format: int = Field(default=DEFINITION_FORMAT_VERSION, ge=1, le=DEFINITION_FORMAT_VERSION)
    id: DefinitionId
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(default="", description="The agent's objective.")
    model: str | None = Field(default=None, max_length=100)
    role: RoleName | None = Field(default=None, description="Assistant Role, by name.")
    approval_policy: AgentApprovalPolicy | None = Field(
        default=None, description="Tool-approval policy. Omit to inherit the global default."
    )
    autonomy: AgentAutonomy = Field(default_factory=AgentAutonomy)
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    limits: AgentLimits = Field(default_factory=AgentLimits)

    @field_validator("format", mode="before")
    @classmethod
    def _format(cls, value: Any) -> Any:
        return _check_format(value)


# --- Workflow ---------------------------------------------------------------


class StepCapabilities(_Strict):
    """Per-step overrides; an omitted toggle inherits the step's agent."""

    mcp: bool | None = None
    skills: bool | None = None
    memory: bool | None = None
    mcp_servers: list[McpServerName] | None = Field(
        default=None,
        description="Only these MCP servers. Omit for every enabled server; [] for none.",
    )

    @model_validator(mode="after")
    def _scope(self) -> StepCapabilities:
        _check_mcp_scope(self.mcp, self.mcp_servers)
        return self


class StepContext(_Strict):
    mode: WorkflowStepContextMode = Field(
        default="auto",
        description=(
            "auto: the previous step's output plus published artifacts; "
            "selected: only the steps listed in `from`; none: the objective alone."
        ),
    )
    sources: list[StepKey] | None = Field(
        default=None,
        alias="from",
        description="Earlier step keys to inherit from (mode: selected only).",
    )

    @model_validator(mode="after")
    def _sources(self) -> StepContext:
        if self.mode == "selected":
            if not self.sources:
                raise ValueError("mode 'selected' needs at least one step key in `from`")
            if len(set(self.sources)) != len(self.sources):
                raise ValueError("`from` lists a step more than once")
        elif self.sources is not None:
            raise ValueError("`from` is only used with mode 'selected'")
        return self


def _check_agent_path(value: str) -> str:
    if "\\" in value:
        raise ValueError("use forward slashes in agent paths")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or value != str(path):
        raise ValueError(
            "agent paths are relative to the definitions folder, e.g. agents/triage.agent.yaml"
        )
    if not value.endswith(AGENT_FILE_SUFFIX) or path.name == AGENT_FILE_SUFFIX:
        raise ValueError(f"agent paths must point at a *{AGENT_FILE_SUFFIX} file")
    return value


class WorkflowStepDefinition(_Strict):
    key: StepKey
    name: str | None = Field(default=None, max_length=200)
    kind: WorkflowStepKind = Field(
        default="task",
        description=(
            "task: runs an agent; inline: runs its own prompt; gate: votes PASS/FAIL "
            "and loops back on FAIL; approval: waits for a human."
        ),
    )
    agent: str | None = Field(
        default=None, description="Agent file, relative to the definitions folder."
    )
    prompt: str | None = Field(
        default=None, description="A one-off objective owned by this step (no agent file)."
    )
    model: str | None = Field(default=None, max_length=100, description="Model for `prompt`.")
    instructions: str | None = Field(
        default=None,
        max_length=8000,
        description="Extra mandate for this step only. Supports {{run.input}}, "
        "{{step.<key>.output}} (or {{step.N.output}}, N = 0-based position) and "
        "{{state.<key>}} placeholders.",
    )
    on_fail: StepKey | None = Field(
        default=None,
        description="Gate/approval: the step to re-drive on FAIL or rework "
        "(default: the previous runnable step).",
    )
    on_error: WorkflowStepErrorPolicy = "fail"
    max_retries: int = Field(default=0, ge=0, le=10)
    on_reject: WorkflowStepRejectPolicy = Field(
        default="rework", description="Approval only: what a human rejection does."
    )
    context: StepContext = Field(default_factory=StepContext)
    capabilities: StepCapabilities = Field(default_factory=StepCapabilities)

    @field_validator("agent")
    @classmethod
    def _agent_path(cls, value: str | None) -> str | None:
        return None if value is None else _check_agent_path(value)

    @model_validator(mode="after")
    def _shape(self) -> WorkflowStepDefinition:
        if self.prompt is not None and not self.prompt.strip():
            raise ValueError("prompt is empty")
        has_agent, has_prompt = self.agent is not None, self.prompt is not None
        if self.kind == "approval":
            if has_agent or has_prompt or self.model is not None:
                raise ValueError("an approval step waits for a human: no agent, prompt or model")
            if {"on_error", "max_retries"} & self.model_fields_set:
                raise ValueError("an approval step has no agent to fail: drop on_error/max_retries")
        elif self.kind == "inline":
            if not has_prompt or has_agent:
                raise ValueError("an inline step needs a prompt and no agent")
        elif has_agent and has_prompt:
            raise ValueError(f"a {self.kind} step has both an agent and a prompt; keep one")
        elif not has_agent and not has_prompt:
            raise ValueError(f"a {self.kind} step needs an agent or a prompt")
        if self.model is not None and not has_prompt:
            raise ValueError("model only applies to a step's own prompt; set it in the agent file")
        if self.on_fail is not None and self.kind not in ("gate", "approval"):
            raise ValueError("on_fail only applies to gate and approval steps")
        if "on_reject" in self.model_fields_set and self.kind != "approval":
            raise ValueError("on_reject only applies to approval steps")
        if self.max_retries and self.on_error != "retry":
            raise ValueError("max_retries needs on_error: retry")
        return self


class WorkflowDefinition(_Strict):
    """``workflows/<slug>.workflow.yaml``."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"title": "Precursor workflow definition"},
    )

    kind: Literal["workflow"]
    format: int = Field(default=DEFINITION_FORMAT_VERSION, ge=1, le=DEFINITION_FORMAT_VERSION)
    id: DefinitionId
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=40, description="A lucide icon name.")
    color: str | None = Field(default=None, max_length=24)
    role: RoleName | None = Field(
        default=None, description="Assistant Role applied to every step, by name."
    )
    approval_policy: AgentApprovalPolicy | None = Field(
        default=None,
        description="Tool-approval policy for every step. Omit to keep each agent's own.",
    )
    clear_artifacts: bool = Field(
        default=True, description="Wipe the agents' previous artifacts at the start of a run."
    )
    max_loops: int = Field(default=3, ge=1, le=25, description="Cap on gate loop-backs per run.")
    step_timeout_seconds: int | None = Field(
        default=None, ge=30, le=86400, description="Stall watchdog. Omit for none."
    )
    steps: list[WorkflowStepDefinition] = Field(default_factory=list)

    @field_validator("format", mode="before")
    @classmethod
    def _format(cls, value: Any) -> Any:
        return _check_format(value)

    @model_validator(mode="after")
    def _references(self) -> WorkflowDefinition:
        position: dict[str, int] = {}
        for idx, step in enumerate(self.steps):
            if step.key in position:
                raise ValueError(f"step key '{step.key}' is used more than once")
            position[step.key] = idx
        for idx, step in enumerate(self.steps):
            if step.on_fail is not None:
                target = position.get(step.on_fail)
                if target is None:
                    raise ValueError(f"step '{step.key}': on_fail '{step.on_fail}' is not a step")
                if target == idx:
                    raise ValueError(f"step '{step.key}': on_fail cannot point at itself")
            for source in step.context.sources or []:
                at = position.get(source)
                if at is None:
                    raise ValueError(f"step '{step.key}': context source '{source}' is not a step")
                if at >= idx:
                    raise ValueError(
                        f"step '{step.key}': context source '{source}' must be an earlier step"
                    )
        return self


# --- Summary template -------------------------------------------------------


class SummaryDefinition(_Strict):
    """``summaries/<slug>.summary.yaml``: how a live session's recap is written."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"title": "Precursor summary template"},
    )

    kind: Literal["summary"]
    format: int = Field(default=DEFINITION_FORMAT_VERSION, ge=1, le=DEFINITION_FORMAT_VERSION)
    id: Annotated[
        str,
        Field(
            pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
            description=(
                "Stable identity, remembered as the last template used. The id of a "
                "built-in template replaces that template."
            ),
        ),
    ]
    name: str = Field(
        min_length=1, max_length=SUMMARY_NAME_MAX, description="Shown in the template picker."
    )
    description: str | None = Field(
        default=None, max_length=300, description="One line on what the recap looks like."
    )
    prompt: str = Field(
        min_length=1,
        max_length=16000,
        description=(
            "Instructions for the model: the sections, tone and length of the recap. "
            "It receives the transcript, notes, insights and linked context; the "
            "language is added for you."
        ),
    )

    @field_validator("format", mode="before")
    @classmethod
    def _format(cls, value: Any) -> Any:
        return _check_format(value)

    @field_validator("prompt")
    @classmethod
    def _prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt is empty")
        return value


DEFINITION_MODELS: dict[
    str, type[AgentDefinition] | type[WorkflowDefinition] | type[SummaryDefinition]
] = {
    "agent": AgentDefinition,
    "workflow": WorkflowDefinition,
    "summary": SummaryDefinition,
}


class _NoFieldTitles(GenerateJsonSchema):
    """Leave out the titles Pydantic derives from field names.

    ``agent`` → ``"title": "Agent"`` says nothing the key doesn't, and editors
    show it in hovers — once per branch of an optional field's ``anyOf``.
    """

    def field_title_should_be_set(self, schema: Any) -> bool:
        return False


def definition_json_schema(kind: DefinitionFileKind) -> dict[str, Any]:
    """The published JSON Schema for one kind of definition file."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **DEFINITION_MODELS[kind].model_json_schema(schema_generator=_NoFieldTitles),
    }
