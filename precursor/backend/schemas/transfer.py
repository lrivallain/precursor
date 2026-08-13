"""Portable YAML documents for agents and workflows.

A *transfer document* is the shareable, human-editable form of one agent or one
workflow: its definition and nothing else. Runtime state (status, run history,
artifacts, token counters, the SDK session handle) is deliberately absent — the
file describes *what to run*, never *what happened*. Secrets are absent too:
webhook tokens are per-install credentials, so an import mints none and the
owner re-mints on the target machine.

A workflow document embeds every agent its steps reference, because a pipeline
that arrives without its agents isn't runnable. Embedded agents are what the
import's conflict resolution acts on: when an incoming agent's name matches one
that already exists, the importer picks ``replace`` / ``create`` / ``link`` per
agent (see :class:`TransferResolution`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from precursor.backend.schemas.agent import AgentApprovalPolicy
from precursor.backend.schemas.workflow import (
    WorkflowStepContextMode,
    WorkflowStepErrorPolicy,
    WorkflowStepKind,
    WorkflowStepRejectPolicy,
)

# Bumped when the document shape changes incompatibly. The importer refuses a
# major it doesn't understand rather than silently dropping fields.
TRANSFER_FORMAT_VERSION = 1

TransferKind = Literal["agent", "workflow"]

# What to do with an incoming object whose name collides with an existing one.
# ``link`` is only meaningful for agents (reference the existing row untouched);
# a workflow collision offers ``replace`` or ``create`` only.
ConflictAction = Literal["replace", "create", "link"]


class TransferRole(BaseModel):
    """An Assistant Role carried alongside whatever referenced it.

    Roles are matched by name on import (they have no portable id of their own),
    and default to ``link`` so importing two workflows that share a persona
    doesn't end up with two copies of it.
    """

    name: str = Field(min_length=1, max_length=64)
    system_prompt: str = ""


class TransferSchedule(BaseModel):
    """Recurrence carried with an agent or workflow.

    Always imported **disabled**: a file dropped into a new install should not
    start firing on a cadence its new owner never asked for.
    """

    interval_seconds: int | None = Field(default=None, ge=60)
    run_at_minute: int | None = Field(default=None, ge=0, le=1439)
    timezone: str = "UTC"
    days_of_week: int = Field(default=127, ge=0, le=127)
    # Agent schedules only: whether each run starts from a clean context.
    clear_context: bool = True


class TransferAgent(BaseModel):
    """One agent's portable definition."""

    # Stable portable identity, minted on first export. Lets a re-import update
    # the very agent the file came from instead of matching it by name.
    export_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    task: str = ""
    model: str | None = None
    autonomy_enabled: bool = False
    max_steps: int = Field(default=12, ge=1, le=100)
    approval_policy: AgentApprovalPolicy | None = None
    token_budget: int | None = Field(default=None, ge=1)
    max_retries: int = Field(default=0, ge=0, le=10)
    use_mcp: bool = True
    use_skills: bool = True
    use_memory: bool = True
    role: TransferRole | None = None
    schedule: TransferSchedule | None = None
    # True when this agent is a workflow step's private vessel. Such agents are
    # never conflict-checked: they belong to their step, not to the roster.
    inline: bool = False


class TransferStep(BaseModel):
    """One workflow step. References its agent by list index, not by database id.

    Ids are install-local, so the document addresses agents positionally within
    its own ``agents`` list — which also keeps the file readable and editable by
    hand. ``null`` for an ``approval`` step, which runs no agent.
    """

    agent: int | None = Field(default=None, ge=0)
    name: str | None = Field(default=None, max_length=200)
    kind: WorkflowStepKind = "task"
    on_fail_position: int | None = Field(default=None, ge=0)
    instructions: str | None = Field(default=None, max_length=8000)
    on_error: WorkflowStepErrorPolicy = "fail"
    max_retries: int = Field(default=0, ge=0, le=10)
    on_reject: WorkflowStepRejectPolicy = "rework"
    context_mode: WorkflowStepContextMode = "auto"
    context_sources: str | None = Field(default=None, max_length=200)
    use_mcp: bool | None = None
    use_skills: bool | None = None
    use_memory: bool | None = None
    # Comma-separated MCP server allowlist. Carried verbatim: the importing
    # machine may have a different server set, and a name it doesn't know
    # simply matches nothing rather than blocking the import.
    mcp_servers: str | None = Field(default=None, max_length=400)


class TransferWorkflow(BaseModel):
    """One workflow's portable definition (steps reference ``agents`` by index)."""

    export_id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=24)
    clear_artifacts: bool = True
    max_loops: int = Field(default=3, ge=1, le=25)
    step_timeout_seconds: int | None = Field(default=None, ge=30, le=86400)
    role: TransferRole | None = None
    schedule: TransferSchedule | None = None
    steps: list[TransferStep] = []


class TransferDocument(BaseModel):
    """The whole YAML file: a format header plus exactly one exported object.

    One object per file, so a document is self-describing and diff-friendly. A
    workflow still carries the agents its steps need under ``agents`` — those are
    dependencies, not co-equal exports.
    """

    model_config = ConfigDict(populate_by_name=True)

    precursor_format: int = Field(default=TRANSFER_FORMAT_VERSION, alias="format")
    kind: TransferKind
    # Free-form provenance for the reader ("exported from Precursor 2026.1").
    exported_by: str | None = None
    exported_at: str | None = None
    # Every agent the document needs. For ``kind: agent`` this holds exactly the
    # exported agent; for ``kind: workflow`` it holds each step's agent, indexed
    # by ``TransferStep.agent``.
    agents: list[TransferAgent] = []
    workflow: TransferWorkflow | None = None


# --- Import preview / apply -------------------------------------------------


class TransferConflict(BaseModel):
    """A name collision the importer needs a decision for."""

    # "agent" or "workflow" — the workflow's own name can collide too.
    kind: Literal["agent", "workflow"]
    # Index into ``TransferDocument.agents`` for an agent conflict; null for the
    # workflow itself. This is the key the resolution is keyed by.
    index: int | None = None
    # The incoming name that collided.
    name: str
    # The existing row it collided with.
    existing_id: int
    existing_title: str
    # True when the match was made on ``export_id`` rather than the name — i.e.
    # this really *is* the same object, previously exported from here.
    same_object: bool = False
    # How many live workflows already reference the existing agent. Surfaced so
    # "replace" makes it obvious the edit ripples into other pipelines.
    workflow_count: int = 0
    # Which actions make sense here (a workflow can't be "linked").
    allowed: list[ConflictAction] = []
    # What the importer will do if the caller sends no explicit resolution.
    default: ConflictAction = "create"


class TransferWarning(BaseModel):
    """A non-blocking note about something the target install can't honour."""

    # e.g. "model" (pinned model not available here), "schedule" (imported off).
    code: str
    message: str


class TransferPreview(BaseModel):
    """What an import *would* do, before anything is written."""

    kind: TransferKind
    name: str
    # Counts for the summary line ("1 workflow, 3 agents").
    agent_count: int = 0
    step_count: int = 0
    conflicts: list[TransferConflict] = []
    warnings: list[TransferWarning] = []


class TransferResolution(BaseModel):
    """The caller's decision for one conflict, keyed the same way as the preview."""

    kind: Literal["agent", "workflow"] = "agent"
    index: int | None = None
    action: ConflictAction


class TransferImportRequest(BaseModel):
    """Apply a document, with one resolution per conflict the preview reported.

    Conflicts left unresolved fall back to the preview's ``default`` action, so a
    scripted import without a UI still behaves predictably (never destructively:
    the default is only ``replace`` when the object is provably the same one).
    """

    content: str = Field(min_length=1, max_length=2_000_000)
    resolutions: list[TransferResolution] = []


class TransferParseRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2_000_000)


class TransferImportResult(BaseModel):
    kind: TransferKind
    # The created/updated object, for the UI to navigate to.
    workflow_id: int | None = None
    agent_id: int | None = None
    name: str
    created_agent_ids: list[int] = []
    replaced_agent_ids: list[int] = []
    linked_agent_ids: list[int] = []
    warnings: list[TransferWarning] = []
