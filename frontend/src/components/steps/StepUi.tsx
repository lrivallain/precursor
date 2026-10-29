import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Loader2, MoreHorizontal, Sparkles, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  DISPLAY_STATUS_LABEL,
  DISPLAY_STATUS_PILL,
  PLAN_STATUS_LABEL,
  PLAN_STATUS_PILL,
  displayStatus,
} from "../../lib/stepPlans";
import type { Step, StepPlanStatus } from "../../lib/types";

export function StepStatusPill({ step, className = "" }: { step: Step; className?: string }) {
  const status = displayStatus(step);
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${DISPLAY_STATUS_PILL[status]} ${className}`}
    >
      {DISPLAY_STATUS_LABEL[status]}
    </span>
  );
}

export interface MenuItem {
  label: string;
  icon?: LucideIcon;
  onSelect: () => void | Promise<void>;
  disabledReason?: string;
  danger?: boolean;
  /** Draws a divider above the item. */
  separated?: boolean;
}

/**
 * The "⋯" menu that keeps secondary actions out of sight, so each screen shows
 * one obvious next action. Items that can't run stay listed with their reason.
 */
export function OverflowMenu({
  items,
  label = "More actions",
  align = "right",
}: {
  items: MenuItem[];
  label?: string;
  align?: "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (items.length === 0) return null;
  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        data-tooltip={label}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-border text-muted transition hover:bg-surface hover:text-fg"
      >
        <MoreHorizontal size={16} />
      </button>
      {open && (
        <div
          role="menu"
          aria-label={label}
          className={`absolute z-30 mt-1 min-w-52 rounded-lg border border-border bg-bg py-1 shadow-lg ${
            align === "right" ? "right-0" : "left-0"
          }`}
        >
          {items.map((item) => {
            const Icon = item.icon;
            return (
              <div key={item.label} className={item.separated ? "mt-1 border-t border-border pt-1" : ""}>
                <button
                  type="button"
                  role="menuitem"
                  disabled={item.disabledReason !== undefined}
                  data-tooltip={item.disabledReason}
                  onClick={() => {
                    setOpen(false);
                    void item.onSelect();
                  }}
                  className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50 ${
                    item.danger ? "text-red-600 dark:text-red-300" : "text-fg"
                  }`}
                >
                  {Icon && <Icon size={14} className="shrink-0" aria-hidden />}
                  {item.label}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
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

export function NeedsReviewBadge({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300 ${className}`}
      data-tooltip={"Re-review needed\nThe ask or instruction changed after this was approved, so the approval no longer counts."}
    >
      Review
    </span>
  );
}

export function ProvisionalBadge() {
  return (
    <span
      className="inline-flex shrink-0 items-center rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300"
      data-tooltip={"Provisional\nWritten before every step was approved: not an approved final."}
    >
      Provisional
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
