import { useState } from "react";
import { Gauge, Loader2 } from "lucide-react";
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
 * A model self-assessment: score plus the reasons behind it (what it covers,
 * the evidence, assumptions, gaps and, for a whole plan, contradictions).
 * Shown as out of date when the work changed after it was written.
 */
export function AssessmentPanel({
  title,
  assessment,
  stale,
  disabledReason,
  onAssess,
}: {
  title: string;
  assessment: Assessment | null;
  stale: boolean;
  disabledReason?: string | null;
  onAssess: () => Promise<unknown>;
}) {
  const [busy, setBusy] = useState(false);

  async function run(): Promise<void> {
    setBusy(true);
    try {
      await onAssess();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="space-y-2 rounded-lg border border-border bg-surface/40 p-3"
      aria-label={title}
      data-testid="assessment-panel"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Gauge size={14} className="text-muted" aria-hidden />
        <h4 className="text-xs font-medium uppercase tracking-wide text-muted">{title}</h4>
        {assessment && (
          <>
            <ScoreChip assessment={assessment} stale={stale} />
            <span className="text-xs text-muted">/ 100</span>
          </>
        )}
        {assessment && stale && (
          <span
            className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300"
            data-tooltip="The work changed after this was assessed. Assess it again."
          >
            Out of date
          </span>
        )}
        <button
          type="button"
          onClick={() => void run()}
          disabled={busy || !!disabledReason}
          data-tooltip={disabledReason ?? "Ask the model to assess this again"}
          className={`${BTN_SMALL} ml-auto`}
        >
          {busy && <Loader2 size={12} className="animate-spin" />}
          {busy ? "Assessing…" : assessment ? "Re-assess" : "Assess"}
        </button>
      </div>
      <p className="text-[11px] text-muted">
        The model's own read of how well this answers the ask: a self-assessment, not a calibrated
        probability. Read the reasons, not just the number.
      </p>
      {assessment ? (
        <div className={`space-y-2 ${stale ? "opacity-70" : ""}`}>
          {assessment.summary && <p className="text-sm">{assessment.summary}</p>}
          <Section label="Covered" items={assessment.covered} />
          <Section label="Evidence" items={assessment.evidence} />
          <Section label="Assumptions" items={assessment.assumptions} />
          <Section label="Gaps" items={assessment.gaps} />
          <Section label="Contradictions" items={assessment.contradictions} />
        </div>
      ) : (
        <p className="text-sm text-muted">Not assessed yet.</p>
      )}
    </section>
  );
}
