import { useState } from "react";
import { Loader2, Pause, Play } from "lucide-react";
import { api } from "../../lib/api";
import type { AutomationMode, StepPlan } from "../../lib/types";
import { BTN_PRIMARY, BTN_SECONDARY } from "./StepUi";

type Mutate = <T>(action: () => Promise<T>) => Promise<T | undefined>;

const MODES: { value: AutomationMode; label: string; hint: string }[] = [
  {
    value: "manual",
    label: "Manual",
    hint: "You write and approve everything. The model only answers when you ask.",
  },
  {
    value: "assisted",
    label: "Assisted",
    hint: "The model drafts a step when you ask and scores it. You review and approve.",
  },
  {
    value: "auto",
    label: "Automated",
    hint: "The model drafts and scores every step, then a provisional final output. It never approves: you review step by step.",
  },
];

/**
 * How much of the work the model does, and the run controls for the automated
 * mode. `canRun` is false once the model has nothing left to do, so the plan's
 * next-action button takes over. Sized for the top bar.
 */
export function AutomationBar({ plan, mutate, canRun }: { plan: StepPlan; mutate: Mutate; canRun: boolean }) {
  const [busy, setBusy] = useState(false);
  const mode = plan.automation_mode;
  const running = plan.auto_state === "running";
  const paused = plan.auto_state === "paused";
  const current = plan.steps.find((st) => st.id === plan.auto_step_id);
  const runningLabel = current ? `Drafting “${current.title}”` : "Running";
  const workable = plan.status === "active" || plan.status === "consolidating";

  async function run(action: () => Promise<unknown>): Promise<void> {
    setBusy(true);
    try {
      await mutate(action);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex shrink-0 items-center gap-2" data-testid="automation-bar">
      <div
        className="inline-flex overflow-hidden rounded-lg border border-border text-xs"
        role="radiogroup"
        aria-label="Automation mode"
      >
        {MODES.map((m) => (
          <button
            key={m.value}
            type="button"
            role="radio"
            aria-checked={mode === m.value}
            disabled={busy || running}
            onClick={() => void run(() => api.stepPlans.update(plan.id, { automation_mode: m.value }))}
            data-tooltip={m.hint}
            className={`px-2.5 py-1 disabled:cursor-not-allowed ${
              mode === m.value ? "bg-accent/15 font-medium text-accent" : "text-muted hover:bg-surface"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>
      {mode === "auto" && workable && (
        <>
          {running ? (
            <button
              type="button"
              className={BTN_SECONDARY}
              disabled={busy}
              onClick={() => void run(() => api.stepPlans.pauseAuto(plan.id))}
              data-tooltip="Stop after the step in flight. Nothing is lost."
            >
              <Pause size={14} /> Pause
            </button>
          ) : canRun || paused ? (
            <button
              type="button"
              className={BTN_PRIMARY}
              disabled={busy}
              onClick={() => void run(() => api.stepPlans.startAuto(plan.id))}
              data-tooltip="Draft and score the steps that have no artifact yet. Approved steps and your edits are never touched."
            >
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
              {paused ? "Resume" : "Run"}
            </button>
          ) : null}
          {running && (
            <span
              className="inline-flex items-center gap-1.5 text-xs text-muted"
              role="status"
              data-tooltip="Drafting and scoring the steps that have no artifact yet"
            >
              <Loader2 size={12} className="animate-spin" /> {runningLabel}
            </span>
          )}
          {paused && <span className="text-xs text-amber-700 dark:text-amber-300">Paused</span>}
        </>
      )}
    </div>
  );
}
