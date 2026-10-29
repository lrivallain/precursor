import { useState } from "react";
import { ChevronDown, ChevronRight, Play } from "lucide-react";
import { api } from "../../lib/api";
import { dropDraft, markFailed, planFieldKey, useFieldDraft } from "../../lib/stepDrafts";
import { buildStepTree } from "../../lib/stepPlans";
import type { StepPlan } from "../../lib/types";
import { OutputInstructions } from "./ConsolidationPanel";
import { StepEditor } from "./StepEditor";
import { StepTree } from "./StepTree";
import { BTN_PRIMARY, BTN_SECONDARY, GuidedAction, INPUT } from "./StepUi";

type Mutate = <T>(action: () => Promise<T>) => Promise<T | undefined>;

/**
 * Before work starts: review the ask, shape the list of steps (accept or
 * dismiss the model's suggestions, edit, add, reorder), then start.
 */
export function DraftingView({
  plan,
  mutate,
  selectedId,
  onSelect,
}: {
  plan: StepPlan;
  mutate: Mutate;
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  const roots = buildStepTree(plan.steps);
  const proposed = plan.steps.filter((s) => s.status === "proposed").length;
  const accepted = plan.steps.length - proposed;
  const [askOpen, setAskOpen] = useState(plan.steps.length === 0);
  const askKey = planFieldKey(plan.id, "ask");
  const askField = useFieldDraft(askKey, plan.source_text, true);
  const ask = askField.value;
  const [starting, setStarting] = useState(false);

  async function saveAsk(): Promise<void> {
    if (ask === plan.source_text) return;
    const saved = await mutate(() => api.stepPlans.update(plan.id, { source_text: ask }));
    if (saved === undefined) markFailed([askKey], true);
    else dropDraft(askKey);
  }

  async function start(acceptProposed: boolean): Promise<void> {
    setStarting(true);
    try {
      await mutate(() => api.stepPlans.start(plan.id, acceptProposed));
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 p-5">
      <section className="rounded-xl border border-border bg-surface">
        <button
          type="button"
          onClick={() => setAskOpen((v) => !v)}
          aria-expanded={askOpen}
          className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm font-medium"
        >
          {askOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          The ask
          <span className="text-xs font-normal text-muted">
            {plan.source_text.trim().split(/\s+/).filter(Boolean).length} words
          </span>
        </button>
        {askOpen && (
          <div className="px-4 pb-4">
            <textarea
              value={ask}
              onChange={(e) => askField.setValue(e.target.value)}
              onBlur={() => void saveAsk()}
              rows={10}
              aria-label="The ask"
              className={`${INPUT} resize-y font-normal`}
            />
            {askField.failed && (
              <p role="alert" className="mt-1 flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
                Not saved: your text is kept here.
                <button type="button" className="underline" onClick={() => void saveAsk()}>
                  Retry
                </button>
              </p>
            )}
            <p className="mt-1 text-xs text-muted">
              The text the steps are drawn from. Edits apply the next time the model proposes steps.
            </p>
          </div>
        )}
      </section>

      <OutputInstructions plan={plan} mutate={mutate} />

      <section className="space-y-3" aria-label="Steps">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">
            Steps
            <span className="ml-2 text-xs font-normal text-muted">
              {accepted} planned{proposed > 0 ? ` · ${proposed} suggested` : ""}
            </span>
          </h2>
        </div>
        <StepTree
          planId={plan.id}
          roots={roots}
          selectedId={selectedId}
          onSelect={onSelect}
          mutate={mutate}
          emptyMessage="No steps yet. Let the model propose some, or add your own."
          renderDetail={(node) => (
            <div className="pt-1">
              <StepEditor step={node.step} mutate={mutate} compact />
            </div>
          )}
        />
        <div className="rounded-xl border border-border p-3">
          <GuidedAction
            label={plan.steps.length === 0 ? "Propose steps" : "Re-propose steps"}
            busyLabel="Proposing…"
            secondary
            guidanceLabel="What the model should do differently (optional)"
            placeholder="e.g. group the legal questions together, keep it to 6 steps"
            onRun={(guidance) => mutate(() => api.stepPlans.propose(plan.id, guidance || undefined))}
          >
            {proposed > 0 && (
              <span className="text-xs text-muted">Replaces the suggestions you haven&apos;t accepted.</span>
            )}
          </GuidedAction>
        </div>
      </section>

      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
        <button
          type="button"
          onClick={() => void start(true)}
          disabled={starting || plan.steps.length === 0}
          className={BTN_PRIMARY}
          data-tooltip={plan.steps.length === 0 ? "Add at least one step first" : undefined}
        >
          <Play size={14} />
          {proposed > 0 ? "Accept suggestions & start" : "Start"}
        </button>
        {proposed > 0 && (
          <button
            type="button"
            onClick={() => void start(false)}
            disabled={starting || accepted === 0}
            className={BTN_SECONDARY}
            data-tooltip={
              accepted === 0
                ? "Accept at least one step first"
                : "Drop the suggestions you haven't accepted and start with the rest"
            }
          >
            Start without suggestions
          </button>
        )}
      </div>
    </div>
  );
}
