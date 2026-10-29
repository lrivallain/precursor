import { useState } from "react";
import type { ReactNode } from "react";
import { Loader2, Sparkles, X } from "lucide-react";
import {
  PLAN_STATUS_LABEL,
  PLAN_STATUS_PILL,
  STEP_STATUS_LABEL,
  STEP_STATUS_PILL,
} from "../../lib/stepPlans";
import type { StepPlanStatus, StepStatus } from "../../lib/types";

export function StepStatusPill({ status, className = "" }: { status: StepStatus; className?: string }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${STEP_STATUS_PILL[status]} ${className}`}
    >
      {STEP_STATUS_LABEL[status]}
    </span>
  );
}

export function PlanStatusPill({ status }: { status: StepPlanStatus }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${PLAN_STATUS_PILL[status]}`}
    >
      {PLAN_STATUS_LABEL[status]}
    </span>
  );
}

export function StaleBadge({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300 ${className}`}
      data-tooltip={"The final output is out of date\nSteps changed after it was generated"}
    >
      Stale
    </span>
  );
}

export function ErrorBanner({ message, onDismiss }: { message: string; onDismiss?: () => void }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-300"
    >
      <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">{message}</span>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          className="shrink-0 rounded p-0.5 hover:bg-red-500/20"
          aria-label="Dismiss"
          data-tooltip="Dismiss"
        >
          <X size={14} />
        </button>
      )}
    </div>
  );
}

export const BTN_PRIMARY =
  "inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
export const BTN_SECONDARY =
  "inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-fg transition hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50";
export const BTN_SMALL =
  "inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-xs text-fg transition hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50";
export const INPUT =
  "w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm outline-none focus:border-accent/60";

/**
 * A model action with optional guidance: the button runs `onRun(guidance)`, the
 * disclosure above it holds the free-text steer. The model call is slow, so the
 * button spins for as long as `onRun` is pending.
 */
export function GuidedAction({
  label,
  busyLabel,
  guidanceLabel = "Guidance for the model (optional)",
  placeholder,
  disabled,
  disabledReason,
  secondary,
  onRun,
  children,
}: {
  label: string;
  busyLabel: string;
  guidanceLabel?: string;
  placeholder?: string;
  disabled?: boolean;
  disabledReason?: string;
  secondary?: boolean;
  onRun: (guidance: string) => Promise<unknown>;
  /** Extra controls rendered beside the button (e.g. a "force" checkbox). */
  children?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [guidance, setGuidance] = useState("");
  const [busy, setBusy] = useState(false);

  async function run(): Promise<void> {
    setBusy(true);
    try {
      await onRun(guidance.trim());
    } finally {
      setBusy(false);
    }
  }

  const blocked = disabled || busy;
  return (
    <div className="space-y-2">
      {open && (
        <label className="block text-xs text-muted">
          {guidanceLabel}
          <textarea
            value={guidance}
            onChange={(e) => setGuidance(e.target.value)}
            rows={2}
            placeholder={placeholder}
            disabled={busy}
            className={`${INPUT} mt-1`}
          />
        </label>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void run()}
          disabled={blocked}
          className={secondary ? BTN_SECONDARY : BTN_PRIMARY}
          data-tooltip={disabled && disabledReason ? disabledReason : undefined}
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
          {busy ? busyLabel : label}
        </button>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          disabled={busy}
          className="text-xs text-muted underline-offset-2 hover:text-fg hover:underline disabled:opacity-50"
          aria-expanded={open}
        >
          {open ? "Hide guidance" : "Add guidance"}
        </button>
        {children}
      </div>
    </div>
  );
}
