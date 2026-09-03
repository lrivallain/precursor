// Presentation helpers for the Workflows section — the reusable agent-sequence
// orchestrator. Mirrors the tone of `agents.ts`: full Tailwind class strings so
// the compiler keeps them, keyed by the backend `WorkflowStatus`.

import type {
  RecurrenceRule,
  Workflow,
  WorkflowRun,
  WorkflowRunStep,
  WorkflowStatus,
  WorkflowStep,
} from "./types";

export const WORKFLOW_STATUS_LABEL: Record<WorkflowStatus, string> = {
  draft: "Draft",
  idle: "Ready",
  running: "Running",
  paused: "Paused",
  awaiting_approval: "Needs you",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

// Soft badge treatment (translucent fill + text) for inline status pills.
export const WORKFLOW_STATUS_BADGE: Record<WorkflowStatus, string> = {
  draft: "bg-slate-500/15 text-slate-400",
  idle: "bg-indigo-500/15 text-indigo-500",
  running: "bg-sky-500/15 text-sky-500",
  paused: "bg-amber-500/15 text-amber-500",
  awaiting_approval: "bg-violet-500/15 text-violet-500",
  completed: "bg-emerald-500/15 text-emerald-500",
  failed: "bg-red-500/15 text-red-500",
  cancelled: "bg-muted/20 text-muted",
};

// Solid dot colour for status medallions.
export const WORKFLOW_STATUS_DOT: Record<WorkflowStatus, string> = {
  draft: "bg-slate-400",
  idle: "bg-indigo-500",
  running: "bg-sky-500",
  paused: "bg-amber-500",
  awaiting_approval: "bg-violet-500",
  completed: "bg-emerald-500",
  failed: "bg-red-500",
  cancelled: "bg-muted",
};

// Which per-step agent statuses read as "this step finished successfully".
// Mirrors the backend coordinator's STEP_DONE_STATUSES — a plain single-turn
// agent rests at `idle`, not `completed`.
const STEP_DONE = new Set(["idle", "completed"]);
const STEP_FAILED = new Set(["failed", "cancelled"]);

export type WorkflowStepState = "done" | "active" | "failed" | "pending";

// How a run-trace status reads on the coarse step strip. "skipped" resolved (the
// run moved past it), so it reads as done here; the trace row carries the detail.
const TRACE_TO_STATE: Record<string, WorkflowStepState> = {
  running: "active",
  awaiting_approval: "active",
  completed: "done",
  passed: "done",
  skipped: "done",
  failed: "failed",
  blocked: "active",
  // An attempt abandoned before it ran (a duplicate entry, or a row left open by
  // a process that died mid-step). It says nothing about the step's outcome.
  superseded: "pending",
};

/**
 * Derive a step's visual state.
 *
 * Prefer the **run trace** when one is supplied: it says what happened to this
 * step *in this run*. The agent's own status is no good on its own because it
 * survives between runs — a step that finished last time rests at `idle`, so the
 * strip would light up green the instant a fresh run started, before that step
 * had done anything. A step with no trace in the current run is `pending`.
 *
 * Without a run (the gallery, which doesn't load traces) fall back to the live
 * agent status, but never let a step *after* the running cursor read as done —
 * same staleness, cheaper guard.
 */
export function stepState(
  workflow: Workflow,
  step: WorkflowStep,
  run?: WorkflowRun | null,
): WorkflowStepState {
  if (run) {
    // Newest attempt for this position wins (a gate loop-back appends attempts).
    // A manual replay is skipped: it re-runs a step outside the pipeline, so
    // letting it repaint the strip would misreport how the run itself went.
    let latest: WorkflowRunStep | null = null;
    for (const rs of run.step_runs) {
      if (rs.position === step.position && !rs.replay) latest = rs;
    }
    if (!latest) return "pending";
    return TRACE_TO_STATE[latest.status] ?? "pending";
  }

  const status = step.agent?.status ?? null;
  if (status && STEP_FAILED.has(status)) return "failed";
  // An approval step runs no agent, so it can only be read from the run cursor.
  if (workflow.current_step_id === step.id) {
    if (workflow.status === "running" || workflow.status === "awaiting_approval") return "active";
  }
  // Mid-run, anything the cursor hasn't reached yet cannot be done, whatever its
  // agent still says from a previous run.
  const current = workflow.steps.find((s) => s.id === workflow.current_step_id) ?? null;
  if (current && step.position > current.position) return "pending";
  if (status && STEP_DONE.has(status)) return "done";
  if (status === "running" || status === "needs_approval" || status === "blocked") return "active";
  return "pending";
}

export const STEP_STATE_DOT: Record<WorkflowStepState, string> = {
  done: "bg-emerald-500",
  active: "bg-sky-500",
  failed: "bg-red-500",
  pending: "bg-muted",
};

export const STEP_STATE_RING: Record<WorkflowStepState, string> = {
  done: "border-emerald-500/50",
  active: "border-sky-500/60 ring-2 ring-sky-500/30",
  failed: "border-red-500/50",
  pending: "border-border",
};

// A workflow is mid-flight (its controls should offer pause/cancel).
export function workflowIsActive(workflow: Workflow): boolean {
  return (
    workflow.status === "running" ||
    workflow.status === "paused" ||
    workflow.status === "awaiting_approval"
  );
}

// How many steps have completed (for the gallery card progress bar).
export function stepProgress(workflow: Workflow): { done: number; total: number } {
  const total = workflow.steps.length;
  const done = workflow.steps.filter((s) => stepState(workflow, s) === "done").length;
  return { done, total };
}

// A step's display label — its override name, else the agent title, else a
// "missing agent" placeholder (the referenced agent was deleted).
export function stepLabel(step: WorkflowStep): string {
  if (step.name) return step.name;
  if (step.agent?.title) return step.agent.title;
  // An approval step legitimately has no agent — it's a human checkpoint, not a
  // broken reference, so it must never read as "missing".
  if (step.kind === "approval") return "Human approval";
  // An inline step's vessel is hidden, so fall back to its own numbering.
  if (step.kind === "inline") return `Step ${step.position + 1}`;
  return "Missing agent";
}

// A gate step guards the pipeline with a PASS/FAIL verdict and can loop back.
export function isGateStep(step: WorkflowStep): boolean {
  return step.kind === "gate";
}

export function isApprovalStep(step: WorkflowStep): boolean {
  return step.kind === "approval";
}

/** Is the workflow parked on a human approval checkpoint right now? */
export function workflowAwaitsApproval(workflow: Workflow): boolean {
  return workflow.status === "awaiting_approval";
}

/** Compact token count for the cost readouts: 1234 -> "1.2k". */
export function formatTokens(n: number): string {
  if (!n) return "0";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

// The 1-based step number a gate retries on FAIL (for the strip's loop label).
// null when the gate falls back to "previous step".
export function gateRetryLabel(step: WorkflowStep): number | null {
  if ((step.kind !== "gate" && step.kind !== "approval") || step.on_fail_position == null) {
    return null;
  }
  return step.on_fail_position + 1;
}

// Compact relative time ("just now", "5m ago", "3h ago", "2d ago"). Shared with
// the gallery + step modal. Returns "" for a null timestamp.
export function workflowRelativeTime(iso: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 45) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

// Human-readable recurrence summary from the schedule fields, e.g.
// "Every 6h", "Daily at 09:00", "Weekly · Mon,Wed". A schedule with several
// rules joins them with " + ". null when disabled.
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function recurrenceRuleSummary(rule: RecurrenceRule): string {
  const parts: string[] = [];
  const interval = rule.interval_seconds || 86400;
  if (interval % 86400 === 0 && interval >= 86400) {
    const days = interval / 86400;
    parts.push(days === 1 ? "Daily" : `Every ${days}d`);
  } else if (interval % 3600 === 0) {
    parts.push(`Every ${interval / 3600}h`);
  } else {
    parts.push(`Every ${Math.round(interval / 60)}m`);
  }
  if (rule.run_at_minute != null) {
    const h = Math.floor(rule.run_at_minute / 60);
    const m = rule.run_at_minute % 60;
    parts.push(`at ${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`);
  }
  if (rule.days_of_week !== 127) {
    const active = DAY_NAMES.filter((_, i) => (rule.days_of_week & (1 << i)) !== 0);
    if (active.length && active.length < 7) parts.push(`· ${active.join(",")}`);
  }
  return parts.join(" ");
}

export function scheduleSummary(workflow: Workflow): string | null {
  if (!workflow.schedule_enabled) return null;
  // Fall back to the flat primary-rule fields for a server predating `rules`.
  const rules: RecurrenceRule[] = workflow.rules?.length
    ? workflow.rules
    : [
        {
          interval_seconds: workflow.interval_seconds ?? 86400,
          days_of_week: workflow.days_of_week,
          run_at_minute: workflow.run_at_minute,
          timezone: workflow.timezone,
        },
      ];
  return rules.map(recurrenceRuleSummary).join(" + ");
}

// --- Run history / trace presentation --------------------------------------
//
// A WorkflowRun is one execution; its step_runs are per-step *attempts* (a gate
// loop-back appends a fresh attempt). These helpers style the trace timeline,
// run picker, and step-history list, keyed by the durable run/step statuses the
// coordinator writes: run → running|paused|completed|failed|cancelled; step →
// running|passed|completed|failed|blocked|cancelled|superseded.

// Visual metadata for a single step-attempt trace row.
export interface TraceMeta {
  label: string;
  dot: string; // solid dot / rail colour
  text: string; // status text colour
  chip: string; // translucent pill (fill + text)
}

const TRACE_META: Record<string, TraceMeta> = {
  running: {
    label: "Running",
    dot: "bg-sky-500",
    text: "text-sky-500",
    chip: "bg-sky-500/15 text-sky-500",
  },
  passed: {
    label: "Passed",
    dot: "bg-emerald-500",
    text: "text-emerald-500",
    chip: "bg-emerald-500/15 text-emerald-500",
  },
  completed: {
    label: "Done",
    dot: "bg-emerald-500",
    text: "text-emerald-500",
    chip: "bg-emerald-500/15 text-emerald-500",
  },
  failed: {
    label: "Failed",
    dot: "bg-red-500",
    text: "text-red-500",
    chip: "bg-red-500/15 text-red-500",
  },
  blocked: {
    label: "Blocked",
    dot: "bg-amber-500",
    text: "text-amber-500",
    chip: "bg-amber-500/15 text-amber-500",
  },
  awaiting_approval: {
    label: "Needs you",
    dot: "bg-violet-500",
    text: "text-violet-500",
    chip: "bg-violet-500/15 text-violet-500",
  },
  cancelled: {
    label: "Cancelled",
    dot: "bg-muted",
    text: "text-muted",
    chip: "bg-muted/20 text-muted",
  },
  superseded: {
    label: "Superseded",
    dot: "bg-muted",
    text: "text-muted",
    chip: "bg-muted/20 text-muted",
  },
};

const TRACE_FALLBACK: TraceMeta = {
  label: "Pending",
  dot: "bg-muted",
  text: "text-muted",
  chip: "bg-muted/20 text-muted",
};

export function traceMeta(status: string): TraceMeta {
  return TRACE_META[status] ?? TRACE_FALLBACK;
}

// A step-attempt trace is "finished" (its output is final).
export function traceIsDone(step: WorkflowRunStep): boolean {
  return step.status !== "running";
}

// Run-level badge (reuses the workflow status palette where names align, adds a
// "paused" tone). Keyed by the run's own status string.
export const RUN_STATUS_BADGE: Record<string, string> = {
  running: "bg-sky-500/15 text-sky-500",
  paused: "bg-amber-500/15 text-amber-500",
  awaiting_approval: "bg-violet-500/15 text-violet-500",
  completed: "bg-emerald-500/15 text-emerald-500",
  failed: "bg-red-500/15 text-red-500",
  cancelled: "bg-muted/20 text-muted",
};

export const RUN_STATUS_LABEL: Record<string, string> = {
  running: "Running",
  paused: "Paused",
  awaiting_approval: "Awaiting approval",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const RUN_TRIGGER_LABEL: Record<string, string> = {
  manual: "Manual",
  schedule: "Scheduled",
  webhook: "Webhook",
  resume: "Resumed",
};

// Progress of a run: how many *distinct positions* reached a terminal, non-failed
// state over the run's step count. Gate loop-backs (repeated positions) collapse
// so the bar reflects pipeline advancement, not attempt count. Manual replays are
// skipped entirely — replaying a step the run never got past shouldn't advance a
// bar that measures how far the pipeline itself actually got.
export function runProgress(run: WorkflowRun, totalSteps: number): { done: number; total: number } {
  const total = Math.max(totalSteps, 0);
  const donePositions = new Set<number>();
  for (const s of run.step_runs) {
    if (s.replay) continue;
    if (s.status === "completed" || s.status === "passed") donePositions.add(s.position);
  }
  return { done: Math.min(donePositions.size, total || donePositions.size), total };
}

// Format a millisecond span compactly: "820ms", "6s", "2m 5s", "1h 3m".
export function formatDuration(ms: number): string {
  if (ms < 0) ms = 0;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const secs = Math.round(ms / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const remS = secs % 60;
  if (mins < 60) return remS ? `${mins}m ${remS}s` : `${mins}m`;
  const hours = Math.floor(mins / 60);
  const remM = mins % 60;
  return remM ? `${hours}h ${remM}m` : `${hours}h`;
}

// Elapsed wall-clock of a run: start→finish, or start→now while still open.
// "" when the run never started.
export function runElapsed(run: WorkflowRun): string {
  if (!run.started_at) return "";
  const start = new Date(run.started_at).getTime();
  const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end)) return "";
  return formatDuration(end - start);
}

// Duration of a single step attempt (start→finish, or start→now while running).
export function stepElapsed(step: WorkflowRunStep): string {
  if (!step.started_at) return "";
  const start = new Date(step.started_at).getTime();
  const end = step.finished_at ? new Date(step.finished_at).getTime() : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end)) return "";
  return formatDuration(end - start);
}

// A gate verdict pill treatment (PASS = emerald, FAIL = red).
export function gateVerdictChip(verdict: string | null): string {
  if (verdict === "PASS") return "bg-emerald-500/15 text-emerald-500";
  if (verdict === "FAIL") return "bg-red-500/15 text-red-500";
  return "bg-muted/20 text-muted";
}
