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
    final_stale: bool = False
    # Whether consolidating needs no override: every working step is validated.
    ready_to_consolidate: bool = False
    consolidation_chat_id: int | None = None
    consolidation_chat_slug: str | None = None
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


class StepPlanUpdate(ConversationLLMFields):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    source_text: str | None = Field(default=None, max_length=200_000)
    role: str | None = None
    output_instructions: str | None = Field(default=None, max_length=20_000)


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


class FinalWrite(BaseModel):
    content: str = Field(max_length=300_000)


class SendToTopicRequest(BaseModel):
    # What to file; the plan's final output when omitted.
    text: str | None = Field(default=None, max_length=300_000)


class StepPlanReview(BaseModel):
    """The model's read of the plan: gaps and contradictions."""

    text: str
