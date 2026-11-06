import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, Loader2, Pause, Play, Send } from "lucide-react";
import { api, apiErrorMessage } from "../../lib/api";
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

const POLL_MS = 3000;
const READ_TIMEOUT_MS = 12000;

/**
 * A live view of a step plan inside the discussion that started it, using the
 * same status colours and score chips as the Step by step tree.
 */
export function StepPlanCard({ planId }: { planId: number }) {
  const [plan, setPlan] = useState<StepPlan | null>(null);
  const [open, setOpen] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const readSeq = useRef(0);
  const appliedSeq = useRef(0);
  const reads = useRef(new Set<AbortController>());

  const load = useCallback(async () => {
    const seq = ++readSeq.current;
    const controller = new AbortController();
    reads.current.add(controller);
    const timeout = window.setTimeout(() => controller.abort(), READ_TIMEOUT_MS);
    try {
      const next = await api.stepPlans.get(planId, controller.signal);
      if (seq <= appliedSeq.current) return;
      appliedSeq.current = seq;
      setPlan(next);
      setLoadError(null);
    } catch (err) {
      if (seq <= appliedSeq.current) return;
      appliedSeq.current = seq;
      setLoadError(
        controller.signal.aborted
          ? "The plan refresh timed out."
          : apiErrorMessage(err, "The plan could not be refreshed."),
      );
    } finally {
      window.clearTimeout(timeout);
      reads.current.delete(controller);
    }
  }, [planId]);

  useEffect(() => {
    setPlan(null);
    setLoadError(null);
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
      appliedSeq.current = ++readSeq.current;
      for (const controller of reads.current) controller.abort();
      reads.current.clear();
    };
  }, [load, planId]);

  const running = plan != null && plan.archived_at == null && plan.auto_state === "running";
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, load]);

  if (!plan) {
    return loadError ? <p className="mt-3 text-xs text-red-600 dark:text-red-300" role="alert">{loadError}</p> : null;
  }

  const nodes = flattenTree(buildStepTree(plan.steps));
  const archived = plan.archived_at != null;
  if (!archived && nodes.length === 0 && plan.status === "drafting") {
    return (
      <div className="mt-3 text-xs text-muted" data-testid="step-plan-card" data-plan-id={plan.id}>
        No steps yet.{" "}
        <button
          type="button"
          onClick={() => openStepPlan(plan.id)}
          className="font-medium text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Open the plan
        </button>{" "}
        to add or propose some.
        {loadError && <p className="mt-1.5 text-red-600 dark:text-red-300" role="alert">{loadError}</p>}
      </div>
    );
  }
  const { done, total } = planProgress(plan);
  const paused = !archived && plan.automation_mode === "auto" && plan.auto_state === "paused" && plan.status !== "done";
  const finished = plan.status === "done";
  const expanded = open ?? (!finished && !archived);
  const next = planNext(plan);
  const current = plan.steps.find((s) => s.id === plan.auto_step_id);
  const allApproved = total > 0 && done === total;
  const hasFinal = (plan.final_output ?? "").trim() !== "";
  const awaitingReview =
    !archived && !finished && !running && !paused && allApproved && next?.target === "final" && plan.status !== "drafting";
  const finalReady = awaitingReview && hasFinal && !plan.final_stale && !plan.final_provisional;
  const sourceKind = plan.source_chat_id != null ? "chat" : "topic";
  const canSend = finalReady && (plan.source_topic_id != null || plan.source_chat_id != null);
  const lastActivity = plan.activity.at(-1)?.text;
  const stageLabel = archived
    ? "Archived"
    : finished
    ? "Done"
    : paused
    ? "Paused"
    : plan.status === "drafting"
      ? "Suggested steps"
      : awaitingReview
        ? finalReady
          ? "Final output ready, your review"
          : hasFinal
            ? "Final output needs regeneration"
            : "Final output needed"
        : plan.status === "consolidating"
          ? "Final output"
          : "Working";

  const awaitingGo =
    !archived && plan.delegation_policy === "plan" && !running && (
      plan.status === "drafting" ||
      (plan.status === "active" && plan.auto_state === "idle" && plan.amendments.length > 0 &&
        plan.steps.some((step) => step.status === "open" || step.status === "proposed"))
    );

  const launch = async () => {
    setError(null);
    setBusy(true);
    try {
      await api.stepPlans.launch(plan.id);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err, "The run could not start."));
    } finally {
      setBusy(false);
    }
  };

  const sendFinal = async () => {
    setError(null);
    setBusy(true);
    try {
      await api.stepPlans.sendToSource(plan.id);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err, "The final output could not be sent."));
    } finally {
      setBusy(false);
    }
  };

  const regenerateFinal = async () => {
    setError(null);
    setBusy(true);
    try {
      await api.stepPlans.generateFinal(plan.id, { force: true });
      await load();
    } catch (err) {
      setError(apiErrorMessage(err, "The final output could not be generated."));
    } finally {
      setBusy(false);
    }
  };

  const toggleRun = async () => {
    setError(null);
    setBusy(true);
    try {
      if (running) await api.stepPlans.pauseAuto(plan.id);
      else await api.stepPlans.startAuto(plan.id);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err, "The run could not be changed."));
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
        {awaitingGo && (
          <button
            type="button"
            onClick={() => void launch()}
            disabled={busy}
            className="ml-auto inline-flex items-center gap-1 rounded-md bg-accent px-2 py-0.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            <Play size={12} aria-hidden />
            Start the run
          </button>
        )}
        {canSend && (
          <button
            type="button"
            onClick={() => void sendFinal()}
            disabled={busy}
            className="ml-auto inline-flex items-center gap-1 rounded-md bg-accent px-2 py-0.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            <Send size={12} aria-hidden />
            Send to the {sourceKind}
          </button>
        )}
        {awaitingReview && !finalReady && (
          <button
            type="button"
            onClick={() => void regenerateFinal()}
            disabled={busy}
            className="ml-auto inline-flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-xs text-fg hover:bg-surface disabled:opacity-50"
          >
            {hasFinal ? "Regenerate the final output" : "Generate the final output"}
          </button>
        )}
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
        <p className="mt-1.5 flex items-start gap-1.5 break-words text-xs text-sky-700 dark:text-sky-300" role="status">
          <Loader2 size={12} className="animate-spin" aria-hidden />
          <span>{lastActivity ?? (current ? `Working on “${current.title}”…` : "Automated run in progress…")}</span>
        </p>
      )}
      {awaitingGo && (
        <p className="mt-1.5 text-xs text-amber-700 dark:text-amber-300" role="status">
          Plan ready: nothing runs until you start it. Review or amend the steps first.
        </p>
      )}
      {finalReady && (
        <p className="mt-1.5 text-xs text-amber-700 dark:text-amber-300" role="status">
          All steps are approved and the final output is written. {canSend ? `Send it to the ${sourceKind} to complete the plan.` : "Open the plan to review it."}
        </p>
      )}
      {error && <p className="mt-1.5 text-xs text-red-600 dark:text-red-300" role="alert">{error}</p>}
      {loadError && <p className="mt-1.5 text-xs text-red-600 dark:text-red-300" role="alert">{loadError}</p>}
      {paused && (
        <div className="mt-1.5 text-xs text-amber-700 dark:text-amber-300" role="status">
          <p>Run paused. Open the plan to review any blockers before resuming.</p>
          {lastActivity && (
            <p className="mt-1 whitespace-pre-wrap break-words">Most recent activity: {lastActivity}</p>
          )}
        </div>
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
                    const through = continues(level);
                    if (!through) return null;
                    return (
                      <span
                        key={level}
                        className={`absolute w-px bg-border inset-y-0`}
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
                    <Loader2 size={14} className="relative shrink-0 animate-spin text-sky-500" aria-label="Working on this step" />
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
        {canSend || (awaitingReview && !finalReady) ? null : (
        <button
          type="button"
          onClick={() => openStepPlan(plan.id)}
          className="rounded text-xs font-medium text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {archived ? "Open archived plan" : finished || paused || running ? "Open the plan" : (next?.label ?? "Open the plan")}
        </button>
        )}
      </div>
    </div>
  );
}
