"""Step-by-step plans — a long ask worked through as a tree of validated steps.

A plan freezes the ask it came from (``source_text``), carries a tree of steps,
and ends with one consolidated output assembled from the steps' artifacts. Each
step is worked in its own chat (``Chat.step_id``); the plan's final discussion
is a chat too (``Chat.step_plan_id``).

SQLite doesn't enforce the ``ON DELETE SET NULL`` links, so the delete paths in
``services/step_plans.py`` remove the linked chats and clear the topic link
explicitly.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, TimestampMixin

# Plan lifecycle: ``drafting`` while the proposed steps are reviewed, ``active``
# while they are worked, ``consolidating`` once the final output is being built,
# ``done`` once it was filed back (or the user closed the plan).
PLAN_DRAFTING = "drafting"
PLAN_ACTIVE = "active"
PLAN_CONSOLIDATING = "consolidating"
PLAN_DONE = "done"
PLAN_CANCELLED = "cancelled"
STEP_PLAN_STATUSES = (PLAN_DRAFTING, PLAN_ACTIVE, PLAN_CONSOLIDATING, PLAN_DONE, PLAN_CANCELLED)

# Step lifecycle. ``proposed`` is a suggestion (from the model) waiting for the
# user to accept or dismiss it; it is never counted as work to do.
STEP_PROPOSED = "proposed"
STEP_OPEN = "open"
STEP_IN_PROGRESS = "in_progress"
STEP_TO_VALIDATE = "to_validate"
STEP_VALIDATED = "validated"
STEP_CANCELLED = "cancelled"
STEP_STATUSES = (
    STEP_PROPOSED,
    STEP_OPEN,
    STEP_IN_PROGRESS,
    STEP_TO_VALIDATE,
    STEP_VALIDATED,
    STEP_CANCELLED,
)

# How much the model does on its own. ``manual``: nothing unless asked.
# ``assisted``: a model draft is assessed right away. ``auto``: the model can also
# draft every untouched step and build a provisional final in a background run.
# In every mode a person validates; the model only drafts and scores.
AUTOMATION_MANUAL = "manual"
AUTOMATION_ASSISTED = "assisted"
AUTOMATION_AUTO = "auto"
AUTOMATION_MODES = (AUTOMATION_MANUAL, AUTOMATION_ASSISTED, AUTOMATION_AUTO)

# State of the automated run. ``running`` is only trusted while the process holds
# the run (see ``step_plans.RUNNING``); otherwise it reads as ``paused``.
AUTO_IDLE = "idle"
AUTO_RUNNING = "running"
AUTO_PAUSED = "paused"

AUTHOR_USER = "user"
AUTHOR_MODEL = "model"


class StepPlan(Base, TimestampMixin):
    __tablename__ = "step_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # The ask, frozen at creation (editable while drafting). Everything the
    # model does in the plan is grounded in it.
    source_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PLAN_DRAFTING, server_default=PLAN_DRAFTING
    )
    # The topic the plan was started from, and where the final output is filed.
    source_topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True, index=True
    )
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_selection: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What the final output should be (format, tone, language…).
    output_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A validated artifact changed after the final output was built.
    final_stale: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # The final output was built before every working step was validated: it is
    # a draft for review, never an approved result.
    final_provisional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    automation_mode: Mapped[str] = mapped_column(
        String(12), nullable=False, default=AUTOMATION_MANUAL, server_default=AUTOMATION_MANUAL
    )
    auto_state: Mapped[str] = mapped_column(
        String(12), nullable=False, default=AUTO_IDLE, server_default=AUTO_IDLE
    )
    # The model's self-assessment of the whole result (JSON), and the signature
    # of the inputs it looked at: when they change the assessment is stale.
    assessment_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessed_sig: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Step(Base, TimestampMixin):
    __tablename__ = "steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("step_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Steps form a tree: splitting a step makes its pieces children of it.
    parent_step_id: Mapped[int | None] = mapped_column(
        ForeignKey("steps.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Order among siblings.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # What this step must produce.
    instruction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STEP_OPEN, server_default=STEP_OPEN
    )
    # The step's result, Markdown. Every change is kept in ``versions``.
    artifact: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artifact_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_by: Mapped[str] = mapped_column(
        String(8), nullable=False, default=AUTHOR_USER, server_default=AUTHOR_USER
    )
    # The model's self-assessment of this step's artifact (JSON) and the signature
    # (ask + instruction + artifact) it was made against.
    assessment_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessed_sig: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # The ask or this step's instruction changed after it was validated: the
    # approval is kept but no longer counts until the user re-approves it.
    needs_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )


class StepArtifactVersion(Base, TimestampMixin):
    __tablename__ = "step_artifact_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    step_id: Mapped[int] = mapped_column(
        ForeignKey("steps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author: Mapped[str] = mapped_column(String(8), nullable=False, default=AUTHOR_USER)
