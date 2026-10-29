import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Activity, FileCheck2 } from "lucide-react";
import { buildStepTree, flattenTree, isApprovedStep, workingLeaves } from "../../lib/stepPlans";
import type { StepPlan } from "../../lib/types";
import { draftKey, useUnsavedGuard, useUnsavedKeys } from "../../lib/stepDrafts";
import { useResizableWidth } from "../../lib/useResizableWidth";
import { ResizeHandle } from "../ResizeHandle";
import { StaleBadge } from "./StepUi";
import { ConsolidationPanel } from "./ConsolidationPanel";
import { StepDetail } from "./StepDetail";
import { StepTree } from "./StepTree";

function formatTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** What the automated run did and is doing, newest last; open while it runs. */
function ActivityFeed({ plan, onSelect }: { plan: StepPlan; onSelect: (selection: StepSelection) => void }) {
  const running = plan.auto_state === "running";
  const notes = plan.activity;
  const [open, setOpen] = useState(running);
  const wasRunning = useRef(running);
  useEffect(() => {
    if (running && !wasRunning.current) setOpen(true);
    wasRunning.current = running;
  }, [running]);
  const listRef = useRef<HTMLOListElement>(null);
  useEffect(() => {
    if (open && listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [open, notes.length]);
  if (notes.length === 0 && !running) return null;
  return (
    <div className="rounded-xl border border-border" data-testid="activity-feed">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium"
      >
        <Activity size={14} className={running ? "text-sky-600 dark:text-sky-400" : "text-muted"} />
        <span className="flex-1">Activity</span>
        {running && <span className="text-xs font-normal text-sky-600 dark:text-sky-400">running…</span>}
      </button>
      {open && (
        <ol ref={listRef} className="max-h-48 space-y-1 overflow-y-auto border-t border-border px-3 py-2 text-xs" aria-live="polite">
          {notes.length === 0 && <li className="text-muted">Starting…</li>}
          {notes.map((n) => (
            <li key={n.id} className="flex gap-2">
              <span className="shrink-0 tabular-nums text-muted">{formatTime(n.created_at)}</span>
              {n.step_id != null ? (
                <button type="button" className="min-w-0 text-left hover:text-accent" onClick={() => onSelect(n.step_id)}>
                  {n.text}
                </button>
              ) : (
                <span className="min-w-0">{n.text}</span>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

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
  useUnsavedGuard();
  const unsaved = useUnsavedKeys(plan.id);
  const unsavedStepIds = useMemo(
    () => new Set(plan.steps.filter((s) => unsaved.has(draftKey(plan.id, s.id))).map((s) => s.id)),
    [plan.id, plan.steps, unsaved],
  );
  const finalUnsaved = unsaved.has(draftKey(plan.id, "final"));
  const roots = useMemo(() => buildStepTree(plan.steps), [plan.steps]);
  const selectedNode =
    typeof selected === "number" ? flattenTree(roots).find((n) => n.step.id === selected) : undefined;
  const leaves = roots.flatMap(workingLeaves);
  const pending = leaves.length - leaves.filter((l) => isApprovedStep(l.step)).length;
  const hasFinal = (plan.final_output ?? "").trim().length > 0;

  const finalHint = plan.status === "done"
    ? plan.source_topic_id
      ? "Done · sent to the topic"
      : "Done"
    : hasFinal
      ? plan.final_stale
        ? "Out of date"
        : "Written"
      : plan.ready_to_consolidate
        ? "Ready to generate"
        : `${pending} step${pending === 1 ? "" : "s"} left to approve`;

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
          activeStepId={plan.auto_step_id}
          unsavedStepIds={unsavedStepIds}
          footer={
            <div className="space-y-3 pt-2">
              <ActivityFeed plan={plan} onSelect={onSelect} />
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
                    : "Not every step is approved yet. You can still generate, after a confirmation."
                }
              >
                <FileCheck2 size={16} className="shrink-0 text-muted" />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium">Final output</span>
                  <span className="block truncate text-xs text-muted">{finalHint}</span>
                </span>
                {finalUnsaved && (
                  <span
                    className="size-2 shrink-0 rounded-full bg-amber-500"
                    role="img"
                    aria-label="Unsaved edits"
                    data-tooltip="Unsaved edits: they are kept while you look around, until you save or discard them"
                  />
                )}
                {plan.final_stale && hasFinal && <StaleBadge />}
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
              When every step is approved, the final output combines them.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
