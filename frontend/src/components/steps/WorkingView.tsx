import { useMemo, type CSSProperties } from "react";
import { FileCheck2 } from "lucide-react";
import { api } from "../../lib/api";
import { buildStepTree, flattenTree, isApprovedStep, workingLeaves } from "../../lib/stepPlans";
import type { StepPlan } from "../../lib/types";
import { useResizableWidth } from "../../lib/useResizableWidth";
import { ResizeHandle } from "../ResizeHandle";
import { BTN_SECONDARY, GuidedAction, StaleBadge } from "./StepUi";
import { ConsolidationPanel } from "./ConsolidationPanel";
import { StepDetail } from "./StepDetail";
import { StepTree } from "./StepTree";

type Mutate = <T>(action: () => Promise<T>) => Promise<T | undefined>;

export type StepSelection = number | "final" | null;

/**
 * Once the plan has started: the tree on the left (with the pinned final
 * output row), the selected step — or the consolidation — on the right.
 */
export function WorkingView({
  plan,
  mutate,
  reload,
  selected,
  onSelect,
}: {
  plan: StepPlan;
  mutate: Mutate;
  reload: () => Promise<void>;
  selected: StepSelection;
  onSelect: (selection: StepSelection) => void;
}) {
  const { width: treeWidth, onMouseDown: onTreeResize } = useResizableWidth({
    storageKey: "precursor.steps.treeWidth",
    defaultWidth: 368,
    min: 240,
    max: 640,
  });
  const roots = useMemo(() => buildStepTree(plan.steps), [plan.steps]);
  const selectedNode =
    typeof selected === "number" ? flattenTree(roots).find((n) => n.step.id === selected) : undefined;
  const leaves = roots.flatMap(workingLeaves);
  const pending = leaves.length - leaves.filter((l) => isApprovedStep(l.step)).length;
  const hasFinal = (plan.final_output ?? "").trim().length > 0;

  const finalHint = plan.status === "done"
    ? "Sent to the topic"
    : hasFinal
      ? plan.final_stale
        ? "Out of date"
        : "Written"
      : plan.ready_to_consolidate
        ? "Ready to generate"
        : `${pending} step${pending === 1 ? "" : "s"} left to validate`;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
      <div
        className="relative shrink-0 lg:w-[var(--tree-w)]"
        style={{ "--tree-w": `${treeWidth}px` } as CSSProperties}
      >
      <aside
        className="h-full border-b border-border p-3 lg:overflow-y-auto lg:border-b-0 lg:border-r"
        aria-label="Plan steps"
      >
        <StepTree
          planId={plan.id}
          roots={roots}
          selectedId={typeof selected === "number" ? selected : null}
          onSelect={onSelect}
          mutate={mutate}
          emptyMessage="This plan has no steps. Add one to continue."
          footer={
            <div className="space-y-3 pt-2">
              <GuidedAction
                label="Suggest missing steps"
                busyLabel="Looking for gaps…"
                secondary
                guidanceLabel="What to look for (optional)"
                placeholder="e.g. anything about security or migration timing"
                onRun={(guidance) => mutate(() => api.stepPlans.suggestMissing(plan.id, guidance || undefined))}
              />
              <button
                type="button"
                onClick={() => onSelect("final")}
                aria-current={selected === "final" ? "true" : undefined}
                data-testid="final-output-row"
                className={`flex w-full items-center gap-2 rounded-xl border px-3 py-2.5 text-left ${
                  selected === "final" ? "border-accent/60 bg-accent/5" : "border-border hover:bg-surface"
                }`}
                data-tooltip={
                  plan.ready_to_consolidate
                    ? undefined
                    : "Not every step is validated yet. You can still generate, after a confirmation."
                }
              >
                <FileCheck2 size={16} className="shrink-0 text-muted" />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium">Final output</span>
                  <span className="block truncate text-xs text-muted">{finalHint}</span>
                </span>
                {plan.final_stale && hasFinal && <StaleBadge />}
                <span className={`${BTN_SECONDARY} pointer-events-none !px-2 !py-1 text-xs`} aria-hidden>
                  Consolidate
                </span>
              </button>
            </div>
          }
        />
      </aside>
      <div className="hidden lg:block">
        <ResizeHandle onMouseDown={onTreeResize} side="right" />
      </div>
      </div>
      <section className="min-w-0 flex-1 p-5 lg:overflow-y-auto" aria-label="Step detail">
        {selected === "final" ? (
          <ConsolidationPanel plan={plan} mutate={mutate} reload={reload} />
        ) : selectedNode ? (
          <StepDetail
            key={selectedNode.step.id}
            node={selectedNode}
            mode={plan.automation_mode}
            mutate={mutate}
            reload={reload}
            onSelect={onSelect}
          />
        ) : (
          <div className="mx-auto max-w-md space-y-2 pt-16 text-center text-sm text-muted">
            <p className="font-medium text-fg">Pick a step</p>
            <p>
              Select a step on the left to edit it, write or generate its artifact and discuss it.
              When every step is validated, consolidate them into one final output.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
