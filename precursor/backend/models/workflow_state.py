"""WorkflowState — a pipeline's own durable memory, shared by its steps.

The counterpart to :class:`~precursor.backend.models.agent_state.AgentState`, one
level up. An agent's scratchpad belongs to *that agent*; this belongs to the
**pipeline**, which matters because a ``WorkflowStep`` points at an otherwise
independent, **reusable** agent:

* the same "Summariser" agent can be step 2 of three different workflows, so a
  key it wrote under its own scope would be clobbered by whichever pipeline ran
  last — while the value is really a fact about *one* pipeline;
* an ``inline`` agent is owned by a single step and deleted with it, so anything
  it remembers dies with the step rather than outliving the run;
* the interesting facts ("the baseline we compare against", "the last invoice we
  processed") belong to the pipeline as a whole, and are written by one step to
  be read by a *different* one.

Scope is the workflow and the lifetime is **across runs**, which makes this the
one channel that outlives a run. Everything else a step can see is per-run and
transient: ``WorkflowRun.input`` is this run's brief, ``WorkflowRunStep``
captures what each step did, and the artifact blackboard is wiped between runs
when ``Workflow.clear_artifacts`` is set. So a scheduled pipeline that must not
reprocess yesterday's rows has nowhere else to keep its cursor.

Steps consume it two ways: ``{{state.<key>}}`` placeholders substituted into the
step's ``instructions`` (see ``services/workflow_state.render_placeholders``),
and the ``workflow_state_*`` MCP tools for reading or writing at run time.

Values are opaque to Precursor — JSON by convention, but any text is accepted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.workflow import Workflow

# Mirrors the agent-scratchpad guardrails: this is bookkeeping, not a blob store.
WORKFLOW_STATE_MAX_KEY = 120
WORKFLOW_STATE_MAX_VALUE = 100_000
WORKFLOW_STATE_MAX_KEYS = 200


class WorkflowState(Base, TimestampMixin):
    __tablename__ = "workflow_states"
    __table_args__ = (UniqueConstraint("workflow_id", "key", name="uq_workflow_state_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Caller-chosen handle, unique per workflow. Upserted on write, and the name
    # a step addresses in a ``{{state.<key>}}`` placeholder.
    key: Mapped[str] = mapped_column(String(WORKFLOW_STATE_MAX_KEY), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")

    workflow: Mapped[Workflow] = relationship("Workflow", back_populates="states")
