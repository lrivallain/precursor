import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, Loader2, Pause, Play } from "lucide-react";
import { api } from "../../lib/api";
import { eventBus } from "../../lib/events";
import {
  DISPLAY_STATUS_DOT,
  DISPLAY_STATUS_LABEL,
  buildStepTree,
  displayStatus,
  flattenTree,
  openStepPlan,
  planNext,
  planProgress,
} from "../../lib/stepPlans";
import type { StepPlan } from "../../lib/types";
import { ScoreChip } from "./AssessmentPanel";
import { PlanStatusPill } from "./StepUi";

/**
 * A live, read-only view of a step plan inside the discussion that started it:
 * the same status colours and score chips as the Step by step tree, kept current
 * from `step-plan.changed`. Acting on the steps still happens in the plan.
 */
export function StepPlanCard({ planId }: { planId: number }) {
  const [plan, setPlan] = useState<StepPlan | null>(null);
  const [open, setOpen] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setPlan(await api.stepPlans.get(planId));
    } catch {
      // A deleted plan leaves only the receipt; other failures keep the last view.
    }
  }, [planId]);

  useEffect(() => {
    void load();
    eventBus.start();
    let timer: number | undefined;
    const off = eventBus.subscribe((event) => {
      if (event.type !== "step-plan.changed") return;
      if (event.step_plan_id != null && event.step_plan_id !== planId) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => void load(), 250);
    });
    return () => {
      off();
      window.clearTimeout(timer);
    };
  }, [load, planId]);

  if (!plan) return null;

  const nodes = flattenTree(buildStepTree(plan.steps));
  if (nodes.length === 0 && plan.status === "drafting") {
    return (
      <div className="mt-3 flex items-center gap-2 text-xs text-muted" data-testid="step-plan-card">
        <Loader2 size={13} className="animate-spin" aria-hidden /> Splitting the ask into steps…
      </div>
    );
  }
  const { done, total } = planProgress(plan);
  const running = plan.auto_state === "running";
  const paused = plan.automation_mode === "auto" && plan.auto_state === "paused" && plan.status !== "done";
  const finished = plan.status === "done";
  const expanded = open ?? !finished;
  const next = planNext(plan);
  const current = plan.steps.find((s) => s.id === plan.auto_step_id);
  const stageLabel = finished
    ? "Done"
    : plan.status === "drafting"
      ? "Suggested steps"
      : plan.status === "consolidating"
        ? "Final output"
        : "Working";

  const toggleRun = async () => {
    setBusy(true);
    try {
      setPlan(running ? await api.stepPlans.pauseAuto(plan.id) : await api.stepPlans.startAuto(plan.id));
    } catch {
      // the plan's activity feed reports why a run could not start
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 border-t border-border pt-3" data-testid="step-plan-card" data-plan-id={plan.id}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setOpen(!expanded)}
          className="inline-flex min-w-0 items-center gap-1 rounded text-xs font-medium text-muted hover:text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {expanded ? <ChevronDown size={13} aria-hidden /> : <ChevronRight size={13} aria-hidden />}
          Tasks
        </button>
        <PlanStatusPill status={plan.status} />
        <span className="text-xs tabular-nums text-muted">
          {done}/{total} approved · {stageLabel}
        </span>
        {(running || paused) && (
          <button
            type="button"
            onClick={() => void toggleRun()}
            disabled={busy}
            className="ml-auto inline-flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-xs text-fg hover:bg-surface disabled:opacity-50"
          >
            {running ? <Pause size={12} aria-hidden /> : <Play size={12} aria-hidden />}
            {running ? "Pause" : "Resume"}
          </button>
        )}
      </div>

      {running && (
        <p className="mt-1.5 flex items-center gap-1.5 text-xs text-sky-700 dark:text-sky-300" role="status">
          <Loader2 size={12} className="animate-spin" aria-hidden />
          {current ? `Drafting “${current.title}”…` : "Automated run in progress…"}
        </p>
      )}
      {paused && (
        <p className="mt-1.5 text-xs text-amber-700 dark:text-amber-300" role="status">
          Run paused. Resume it, or open the plan to see why.
        </p>
      )}

      {expanded && (
        <div className="mt-2">
          <div className="relative flex items-center gap-2 py-0.5 text-sm font-medium text-fg">
            <span className="absolute bottom-0 left-[7px] top-1/2 w-px bg-border" aria-hidden />
            <span className="relative mx-[3px] h-[9px] w-[9px] shrink-0 rounded-full border-2 border-accent bg-bg" aria-hidden />
            <span className="min-w-0 flex-1 truncate" data-tooltip={plan.title}>
              {plan.title}
            </span>
          </div>
          <ul aria-label={`Steps of ${plan.title}`}>
            {nodes.map(({ step, depth }, i) => {
              const status = displayStatus(step);
              const drafting = step.id === plan.auto_step_id && running;
              const continues = (level: number) => {
                for (let j = i + 1; j < nodes.length; j++) {
                  if (nodes[j].depth < level) return false;
                  if (nodes[j].depth === level) return true;
                }
                return false;
              };
              return (
                <li
                  key={step.id}
                  className={`relative flex min-w-0 items-center gap-2 py-0.5 text-sm ${status === "suggested" ? "opacity-60" : ""}`}
                  style={{ paddingLeft: (depth + 1) * 16 }}
                  data-status={status}
                >
                  {Array.from({ length: depth + 1 }, (_, level) => {
                    const own = level === depth;
                    const through = continues(level);
                    if (!through) return null;
                    return (
                      <span
                        key={level}
                        className={`absolute w-px bg-border ${own ? "bottom-0 top-1/2" : "inset-y-0"}`}
                        style={{ left: level * 16 + 7 }}
                        aria-hidden
                      />
                    );
                  })}
                  <span
                    className="absolute top-0 h-1/2 rounded-bl-lg border-b border-l border-border"
                    style={{ left: depth * 16 + 7, width: 9 }}
                    aria-hidden
                  />
                  {drafting ? (
                    <Loader2 size={14} className="relative shrink-0 animate-spin text-sky-500" aria-label="Drafting" />
                  ) : status === "approved" ? (
                    <CheckCircle2 size={14} className="relative shrink-0 rounded-full bg-bg text-emerald-500" aria-label="Approved" />
                  ) : (
                    <span
                      className={`relative mx-[5px] h-[9px] w-[9px] shrink-0 rounded-full ${DISPLAY_STATUS_DOT[status]}`}
                      role="img"
                      aria-label={DISPLAY_STATUS_LABEL[status]}
                    />
                  )}
                  <span
                    className={`min-w-0 flex-1 truncate ${status === "skipped" ? "text-muted line-through" : "text-fg"}`}
                    data-tooltip={step.title}
                  >
                    {step.title}
                  </span>
                  {step.assessment && <ScoreChip assessment={step.assessment} stale={step.assessment_stale} />}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <div className="mt-2.5 flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => openStepPlan(plan.id)}
          className="rounded text-xs font-medium text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {finished ? "Open the plan" : (next?.label ?? "Open the plan")}
        </button>
      </div>
    </div>
  );
}
