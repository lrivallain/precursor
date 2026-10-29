import { ArrowUpRight, ListChecks } from "lucide-react";
import { openStepPlan, type StepPlanNote } from "../../lib/stepPlans";

export function StepPlanNoteHeader({ note }: {
  note: Pick<StepPlanNote, "version" | "provisional">;
}) {
  return (
    <div
      className="mb-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs text-muted"
      data-testid="step-plan-note-header"
    >
      <span className="inline-flex items-center gap-1.5">
        <ListChecks size={14} aria-hidden />
        Final output
      </span>
      <div className="flex shrink-0 items-center gap-1.5">
        {note.version != null && (
          <span className="rounded border border-border px-1.5 py-0.5 text-[11px] tabular-nums">
            v{note.version}
          </span>
        )}
        {note.provisional && (
          <span
            className="rounded border border-border px-1.5 py-0.5 text-[11px]"
            data-tooltip="Filed before every step was approved"
          >
            Provisional
          </span>
        )}
      </div>
    </div>
  );
}

export function StepPlanLink({ note }: { note: Pick<StepPlanNote, "planId" | "title"> }) {
  return (
    <button
      type="button"
      onClick={() => openStepPlan(note.planId)}
      className="group/plan inline-flex max-w-full items-center gap-1 rounded text-left text-xs text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      data-tooltip="Open this plan"
    >
      <span className="min-w-0 [overflow-wrap:anywhere]">{note.title}</span>
      <ArrowUpRight size={13} className="shrink-0 opacity-70 group-hover/plan:opacity-100" aria-hidden />
    </button>
  );
}
