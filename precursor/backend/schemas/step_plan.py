"""Step-by-step plan schemas (see services/step_plans.py)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from precursor.backend.schemas.conversation_llm import ConversationLLMFields
from precursor.backend.schemas.schedule import UtcDateTime

PlanStatus = Literal["drafting", "active", "consolidating", "done", "cancelled"]
StepStatus = Literal["proposed", "open", "in_progress", "to_validate", "validated", "cancelled"]
# What a client may set directly; ``proposed`` only comes from the model.
SettableStepStatus = Literal["open", "in_progress", "to_validate", "validated", "cancelled"]
Author = Literal["user", "model"]
AutomationMode = Literal["manual", "assisted", "auto"]
AutoState = Literal["idle", "running", "paused"]
PromptSource = Literal["submitted", "topic_message"]


class StepPlanTraceData(BaseModel):
    """Immutable creation snapshot stored in a topic's step_plan message content."""

    kind: Literal["step_plan_trace"] = "step_plan_trace"
    version: Literal[1] = 1
    plan_id: int
    title: str
    prompt: str
    source: PromptSource


class Assessment(BaseModel):
    """A model's self-assessment of a result. Not a calibrated probability."""

    # 0-100: how well the result seems to answer what it was asked.
    score: int = Field(ge=0, le=100)
    summary: str = ""
    # Requirements of the ask / instruction the result addresses.
    covered: list[str] = Field(default_factory=list)
    # Where the support comes from (a source, a quoted line, a step), if known.
    evidence: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    # Requirements not (fully) covered, missing facts, open questions.
    gaps: list[str] = Field(default_factory=list)
    # Plan level only: places where two steps disagree.
    contradictions: list[str] = Field(default_factory=list)
    # Plan level only: what was read. "steps" is the readiness of the plan's
    # artifacts (no final output existed); "final" is the final output's own text.
    scope: Literal["steps", "final"] = "steps"


class ActivityNote(BaseModel):
    """One trace of what the model or the automated run did, for the live feed."""

    id: int
    text: str
    # The step it concerns; None for a note about the whole plan.
    step_id: int | None = None
    created_at: UtcDateTime


class StepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plan_id: int
    parent_step_id: int | None = None
    position: int
    title: str
    instruction: str
    status: StepStatus
    # A step with children has no work of its own: this is the roll-up of its
    # children's states (equal to ``status`` for a leaf).
    effective_status: StepStatus
    artifact: str
    artifact_version: int
    created_by: Author
    # The step's discussion, once started (see ``POST /api/steps/{id}/chat``).
    chat_id: int | None = None
    chat_slug: str | None = None
    # The ask or this instruction changed after validation: re-approve to count it.
    needs_review: bool = False
    assessment: Assessment | None = None
    # The artifact (or the ask / instruction) changed since it was assessed.
    assessment_stale: bool = False
    created_at: UtcDateTime
    updated_at: UtcDateTime


class StepPlanRead(ConversationLLMFields):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    source_text: str
    status: PlanStatus
    source_topic_id: int | None = None
    source_topic_title: str | None = None
    role: str | None = None
    output_instructions: str | None = None
    final_output: str | None = None
    # Which version of the final output this is (0: none yet), and the latest
    # version filed into the topic, if any.
    final_version: int = 0
    final_posted_version: int | None = None
    final_posted_at: UtcDateTime | None = None
    final_stale: bool = False
    # Built before every working step was validated: a draft, not an approved result.
    final_provisional: bool = False
    # Whether consolidating needs no override: every working step is validated.
    ready_to_consolidate: bool = False
    consolidation_chat_id: int | None = None
    consolidation_chat_slug: str | None = None
    automation_mode: AutomationMode = "manual"
    # ``running`` only while the run is alive in this process.
    auto_state: AutoState = "idle"
    # The step being drafted right now, while the run is alive.
    auto_step_id: int | None = None
    # The latest traces of the model and the run, oldest first.
    activity: list[ActivityNote] = Field(default_factory=list)
    # Coverage of the original ask by the whole result (not an average of steps).
    assessment: Assessment | None = None
    assessment_stale: bool = False
    steps: list[StepRead] = Field(default_factory=list)
    created_at: UtcDateTime
    updated_at: UtcDateTime


class StepPlanItem(BaseModel):
    """One plan as listed in the section's sidebar."""

    id: int
    title: str
    status: PlanStatus
    source_topic_id: int | None = None
    source_topic_title: str | None = None
    step_count: int = 0
    validated_count: int = 0
    final_stale: bool = False
    created_at: UtcDateTime
    updated_at: UtcDateTime


class StepPlanCreate(ConversationLLMFields):
    """Start a plan from a topic or from pasted text.

    With ``topic_id`` and neither ``message_id`` nor ``source_text``, the topic's
    latest user message is the ask.
    """

    title: str | None = Field(default=None, max_length=255)
    topic_id: int | None = None
    message_id: int | None = None
    source_text: str | None = Field(default=None, max_length=200_000)
    role: str | None = None
    # Have the model propose the first steps right away.
    propose: bool = True
    automation_mode: AutomationMode = "manual"


class StepPlanUpdate(ConversationLLMFields):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    source_text: str | None = Field(default=None, max_length=200_000)
    role: str | None = None
    output_instructions: str | None = Field(default=None, max_length=20_000)
    automation_mode: AutomationMode | None = None
    # Link the plan to a topic (its discussion becomes background, the final
    # output can be filed there), or ``null`` to unlink it.
    source_topic_id: int | None = None


class StepCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    instruction: str = Field(default="", max_length=20_000)
    parent_step_id: int | None = None
    # Insert before the sibling at this index; the end when omitted.
    position: int | None = Field(default=None, ge=0)


class StepUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    instruction: str | None = Field(default=None, max_length=20_000)


class StepMove(BaseModel):
    parent_step_id: int | None = None
    position: int = Field(ge=0)


class StepStatusUpdate(BaseModel):
    status: SettableStepStatus


class StepArtifactWrite(BaseModel):
    content: str = Field(max_length=200_000)


class StepArtifactVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: int
    content: str
    author: Author
    created_at: UtcDateTime


class FinalVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: int
    content: str
    author: Author
    created_at: UtcDateTime
    # Set when this version was filed into the topic (the latest time, if more than once).
    posted_at: UtcDateTime | None = None
    posted_message_id: int | None = None


class StepArtifactRevert(BaseModel):
    version: int = Field(ge=1)


class StepGenerateRequest(BaseModel):
    """Optional steer for a model action (what to change, what to focus on)."""

    instructions: str | None = Field(default=None, max_length=5_000)


class StepStartRequest(BaseModel):
    """Leave the drafting screen: ``proposed`` steps are kept or dropped."""

    accept_proposed: bool = True


class ConsolidateRequest(BaseModel):
    instructions: str | None = Field(default=None, max_length=5_000)
    # Consolidate although some working steps aren't validated yet.
    force: bool = False


# What a model draft did: "published" saved a marked deliverable as a to-validate
# version; "discussion" means the reply only stayed in the discussion (questions,
# alternatives…); "awaiting_approval" means the turn is parked on a tool approval.
DraftOutcome = Literal["published", "discussion", "awaiting_approval"]


class ArtifactDraftResult(BaseModel):
    step: StepRead
    outcome: DraftOutcome


class FinalDraftResult(BaseModel):
    plan: StepPlanRead
    outcome: DraftOutcome


class UseMessageRequest(BaseModel):
    """Publish a reply of the discussion as the artifact (or final output)."""

    message_id: int


class FinalWrite(BaseModel):
    content: str = Field(max_length=300_000)


class SendToTopicRequest(BaseModel):
    # What to file; the plan's final output when omitted.
    text: str | None = Field(default=None, max_length=300_000)
    # File a provisional final output anyway (it is labelled as such).
    force: bool = False


class StepPlanReview(BaseModel):
    """The model's read of the plan: gaps and contradictions."""

    text: str
