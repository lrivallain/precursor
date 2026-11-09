"""Workflow coordinator — drives a :class:`Workflow` through its steps.

The workflow is the coordinator (n8n/Temporal-style): agents stay plain,
reusable units and never reference each other. Starting a workflow runs step 0's
agent; when that agent *rests* (idle / completed) the manager's completion seam
calls :func:`advance_for_run`, which starts the next step's agent — injecting
the previous step's published artifacts as a kickoff preamble — until the last
step finishes and the workflow is marked ``completed``.

Design notes
------------
* **Idle *or* completed = step done.** A plain single-turn agent rests at
  ``idle`` (not ``completed``) unless it has autonomy/deps, and workflows create
  no agent-deps. So advancement treats both resting states as "this step
  finished successfully"; only ``failed`` / ``blocked`` / ``cancelled`` divert
  the run.
* **Fresh session everywhere.** Every function takes an explicit
  ``AsyncSession`` (opened by the caller) so DB writes commit independently of
  the request/worker that triggered them.
* **Manager is injected** to avoid a circular import (manager → service happens
  lazily inside ``_advance_workflows``).
* **One advance at a time per workflow.** The completion seam fires from a
  fire-and-forget task, so several advances for the same agent can be in flight
  at once; :func:`_workflow_lock` serialises them and each re-reads the run
  state after acquiring it. See :func:`advance_for_run`.

Layout
------
The coordinator lives in :mod:`.workflow_engine`, one module per concern:
``common`` (statuses, the advance lock, step ordering), ``output`` (step output
and gate verdicts), ``context`` (a step's kickoff context), ``steps`` (the
run-step lifecycle), ``advance`` (start + advance), ``control`` (pause, resume,
retry, approve, reject, cancel, permissions), ``replay`` (single-step replays)
and ``sweep`` (the stalled-step sweep). This module re-exports their names so
callers keep importing ``services.agents.workflow``.
"""

from __future__ import annotations

from precursor.backend.services.agents.workflow_engine.advance import (
    _advance_one,
    advance_for_run,
    start_workflow,
)
from precursor.backend.services.agents.workflow_engine.common import (
    STEP_AWAITING_PERMISSION_STATUSES,
    STEP_BLOCKED_STATUSES,
    STEP_CANCELLED_STATUSES,
    STEP_DONE_STATUSES,
    STEP_FAILED_STATUSES,
    _advance_locks,
    _current_run_for,
    _earlier_content_agent_ids,
    _first_runnable,
    _has_precursor_tools,
    _is_runnable,
    _last_assistant_message,
    _load_workflow,
    _next_runnable_idx,
    _ordered_steps,
    _prev_content_idx,
    _prev_runnable_idx,
    _publish,
    _step_label,
    _workflow_lock,
)
from precursor.backend.services.agents.workflow_engine.context import (
    _BARE_APPROVAL,
    _GATE_PREAMBLE,
    _MIRROR_RUN_SCOPE,
    _TASK_PREAMBLE,
    _approval_notes,
    _approval_notes_preamble,
    _build_context,
    _fail_preamble,
    _last_run_summary,
    _no_previous_output_preamble,
    _prev_step_summary,
    _rejection_preamble,
    _run_input,
    _run_input_preamble,
    _run_scoped_artifacts,
    _step_instructions_preamble,
    _superseded_attempt_runs,
    _unblock_preamble,
    collect_prior_artifacts,
    collect_step_context,
)
from precursor.backend.services.agents.workflow_engine.control import (
    _last_failed_position,
    approve_step,
    cancel_workflow,
    pause_workflow,
    reject_step,
    resolve_step_permission,
    resume_workflow,
    retry_step,
)
from precursor.backend.services.agents.workflow_engine.output import (
    _DIRECTIVE_LINE_RE,
    _DROPPED_TURN_REASON,
    _TRUNCATION_NOTE,
    _VERDICT_FAIL_RE,
    _VERDICT_PASS_RE,
    OUTPUT_SUMMARY_CAP,
    _cap_output_summary,
    _clear_agent_artifacts,
    _clear_run_artifacts,
    _parse_verdict,
    _step_output,
    _tidy_gate_result,
    _turn_dropped,
    _verdict_reason,
)
from precursor.backend.services.agents.workflow_engine.replay import (
    _REPLAY_BUSY_STATUSES,
    finalize_replays_for_run,
    replay_step,
    step_attempt_events,
)
from precursor.backend.services.agents.workflow_engine.steps import (
    _advance_from,
    _apply_failure_policy,
    _begin_run,
    _close_run_step,
    _complete_run,
    _enter_step,
    _finalize_run,
    _finalize_run_step,
    _finalize_step_by_position,
    _launch_step,
    _open_agent_run,
    _open_approval_trace,
    _open_run_step,
    _selected_source_positions,
    _snapshot_step_overrides,
    _started_after,
    _supersede_open_run_steps,
)
from precursor.backend.services.agents.workflow_engine.sweep import sweep_stalled_steps

__all__ = [
    "OUTPUT_SUMMARY_CAP",
    "STEP_AWAITING_PERMISSION_STATUSES",
    "STEP_BLOCKED_STATUSES",
    "STEP_CANCELLED_STATUSES",
    "STEP_DONE_STATUSES",
    "STEP_FAILED_STATUSES",
    "_BARE_APPROVAL",
    "_DIRECTIVE_LINE_RE",
    "_DROPPED_TURN_REASON",
    "_GATE_PREAMBLE",
    "_MIRROR_RUN_SCOPE",
    "_REPLAY_BUSY_STATUSES",
    "_TASK_PREAMBLE",
    "_TRUNCATION_NOTE",
    "_VERDICT_FAIL_RE",
    "_VERDICT_PASS_RE",
    "_advance_from",
    "_advance_locks",
    "_advance_one",
    "_apply_failure_policy",
    "_approval_notes",
    "_approval_notes_preamble",
    "_begin_run",
    "_build_context",
    "_cap_output_summary",
    "_clear_agent_artifacts",
    "_clear_run_artifacts",
    "_close_run_step",
    "_complete_run",
    "_current_run_for",
    "_earlier_content_agent_ids",
    "_enter_step",
    "_fail_preamble",
    "_finalize_run",
    "_finalize_run_step",
    "_finalize_step_by_position",
    "_first_runnable",
    "_has_precursor_tools",
    "_is_runnable",
    "_last_assistant_message",
    "_last_failed_position",
    "_last_run_summary",
    "_launch_step",
    "_load_workflow",
    "_next_runnable_idx",
    "_no_previous_output_preamble",
    "_open_agent_run",
    "_open_approval_trace",
    "_open_run_step",
    "_ordered_steps",
    "_parse_verdict",
    "_prev_content_idx",
    "_prev_runnable_idx",
    "_prev_step_summary",
    "_publish",
    "_rejection_preamble",
    "_run_input",
    "_run_input_preamble",
    "_run_scoped_artifacts",
    "_selected_source_positions",
    "_snapshot_step_overrides",
    "_started_after",
    "_step_instructions_preamble",
    "_step_label",
    "_step_output",
    "_supersede_open_run_steps",
    "_superseded_attempt_runs",
    "_tidy_gate_result",
    "_turn_dropped",
    "_unblock_preamble",
    "_verdict_reason",
    "_workflow_lock",
    "advance_for_run",
    "approve_step",
    "cancel_workflow",
    "collect_prior_artifacts",
    "collect_step_context",
    "finalize_replays_for_run",
    "pause_workflow",
    "reject_step",
    "replay_step",
    "resolve_step_permission",
    "resume_workflow",
    "retry_step",
    "start_workflow",
    "step_attempt_events",
    "sweep_stalled_steps",
]
