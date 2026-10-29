import { useState } from "react";
import { ChevronDown, ChevronRight, Gauge, Loader2 } from "lucide-react";
import type { Assessment } from "../../lib/types";
import { BTN_SMALL } from "./StepUi";

function scoreTone(score: number): string {
  if (score >= 75) return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300";
  if (score >= 50) return "bg-amber-500/15 text-amber-700 dark:text-amber-300";
  return "bg-red-500/15 text-red-600 dark:text-red-300";
}

/** A compact score chip for tree rows and headings. */
export function ScoreChip({
  assessment,
  stale,
}: {
  assessment: Assessment;
  stale: boolean;
}) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full px-1.5 py-0.5 text-[11px] font-medium tabular-nums ${
        stale ? "bg-surface text-muted line-through decoration-muted/60" : scoreTone(assessment.score)
      }`}
      data-tooltip={
        stale
          ? `Model self-assessment ${assessment.score}/100, out of date: the work changed since.`
          : `Model self-assessment ${assessment.score}/100. Not a calibrated probability.`
      }
    >
      {assessment.score}
    </span>
  );
}

function Section({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <h5 className="text-[11px] font-medium uppercase tracking-wide text-muted">{label}</h5>
      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-sm">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

/**
 * A model self-assessment, shown as a score chip. Click it for the reasons (what
 * it covers, the evidence, assumptions, gaps and, for a whole plan,
 * contradictions) and to run it again. The model assesses on its own after it
 * drafts; a step you wrote yourself offers "Assess" instead. Shown as out of
 * date when the work changed after it was written.
 */
export function AssessmentPanel({
  title,
  assessment,
  stale,
  disabledReason,
  onAssess,
  defaultOpen = false,
  scopeNote,
}: {
  title: string;
  assessment: Assessment | null;
  stale: boolean;
  disabledReason?: string | null;
  onAssess: () => Promise<unknown>;
  defaultOpen?: boolean;
  /** What this score did and did not read, shown with the reasons. */
  scopeNote?: string | null;
}) {
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(defaultOpen);

  async function run(): Promise<void> {
    setBusy(true);
    try {
      await onAssess();
    } finally {
      setBusy(false);
    }
  }

  if (!assessment) {
    return (
      <button
        type="button"
        onClick={() => void run()}
        disabled={busy || !!disabledReason}
        data-tooltip={disabledReason ?? "Ask the model how well this answers the ask"}
        aria-label={`Assess: ${title}`}
        className="inline-flex w-fit items-center gap-1.5 rounded-full border border-dashed border-border px-2.5 py-0.5 text-xs text-muted transition hover:bg-surface hover:text-fg disabled:cursor-not-allowed disabled:opacity-50"
      >
        {busy ? <Loader2 size={12} className="animate-spin" /> : <Gauge size={12} aria-hidden />}
        {busy ? "Assessing…" : "Assess"}
      </button>
    );
  }

  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <section aria-label={title} data-testid="assessment-panel">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        data-tooltip={open ? "Hide the reasons" : "Show the reasons behind the score"}
        className="inline-flex items-center gap-1.5 rounded-full border border-border px-2 py-0.5 text-xs text-muted transition hover:bg-surface hover:text-fg"
      >
        <Gauge size={12} aria-hidden />
        <span>{title}</span>
        <ScoreChip assessment={assessment} stale={stale} />
        {stale && <span className="text-amber-700 dark:text-amber-300">out of date</span>}
        <Chevron size={12} aria-hidden />
      </button>
      {open && (
        <div className="mt-2 space-y-2 rounded-lg border border-border bg-surface/40 px-3 py-2">
          <p className="text-[11px] text-muted">
            The model's own read of how well this answers the ask: a self-assessment, not a
            calibrated probability. Read the reasons, not just the number.
          </p>
          {scopeNote && (
            <p className="text-[11px] font-medium text-fg" data-testid="assessment-scope">
              {scopeNote}
            </p>
          )}
          <div className={`space-y-2 ${stale ? "opacity-70" : ""}`}>
            {assessment.summary && <p className="text-sm">{assessment.summary}</p>}
            <Section label="Covered" items={assessment.covered} />
            <Section label="Evidence" items={assessment.evidence} />
            <Section label="Assumptions" items={assessment.assumptions} />
            <Section label="Gaps" items={assessment.gaps} />
            <Section label="Contradictions" items={assessment.contradictions} />
          </div>
          <button
            type="button"
            onClick={() => void run()}
            disabled={busy || !!disabledReason}
            data-tooltip={disabledReason ?? "Ask the model to assess this again"}
            className={BTN_SMALL}
          >
            {busy && <Loader2 size={12} className="animate-spin" />}
            {busy ? "Assessing…" : "Re-assess"}
          </button>
        </div>
      )}
    </section>
  );
}
