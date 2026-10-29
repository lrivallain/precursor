import { ArrowUpRight, ListChecks } from "lucide-react";
import { openStepPlan, type StepPlanNote } from "../../lib/stepPlans";

/**
 * The shared metadata band of topic-filed outputs and creation snapshots.
 * The plan link stays separate from the body; outputs also name their version.
 */
export function StepPlanNoteHeader({ note, label = "Final output" }: {
  note: Pick<StepPlanNote, "planId" | "title"> & Partial<Pick<StepPlanNote, "version" | "provisional">>;
  label?: "Final output" | "Started" | "Linked";
}) {
  return (
    <div
      className="-mx-3 -mt-2 mb-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-t-[7px] border-b border-accent/25 bg-accent/10 px-3 py-2"
      data-testid={label === "Final output" ? "step-plan-note-header" : undefined}
    >
      <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-accent/20 text-accent">
        <ListChecks size={14} aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-accent">
          Step by step · {label}
        </div>
        <button
          type="button"
          onClick={() => openStepPlan(note.planId)}
          className="group/plan inline-flex max-w-full items-center gap-1 text-left text-sm font-semibold text-accent hover:underline"
          data-tooltip="Open this plan"
        >
          <span className="truncate">{note.title}</span>
          <ArrowUpRight size={13} className="shrink-0 opacity-70 group-hover/plan:opacity-100" aria-hidden />
        </button>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        {note.version != null && (
          <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[11px] font-medium tabular-nums text-accent">
            v{note.version}
          </span>
        )}
        {note.provisional && (
          <span
            className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300"
            data-tooltip="Filed before every step was approved"
          >
            Provisional
          </span>
        )}
      </div>
    </div>
  );
}
