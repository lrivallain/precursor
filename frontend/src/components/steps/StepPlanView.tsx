import { useEffect, useState } from "react";
import { buildStepTree, flattenTree, stepStartSelection } from "../../lib/stepPlans";
import { useStepPlan } from "../../lib/useStepPlan";
import { api } from "../../lib/api";
import { Markdown } from "../Markdown";
import { SectionLoading } from "../SectionLoading";
import { DraftingView } from "./DraftingView";
import { StepPlanHeader } from "./StepPlanHeader";
import { BTN_SECONDARY, ErrorBanner, StepStatusPill } from "./StepUi";
import { WorkingView, type StepSelection } from "./WorkingView";

/**
 * One step plan: loads it, keeps it fresh (own actions + the server's
 * `step-plan.changed`), and holds the selection so it stays put across refetches.
 * Mounted with `key={planId}`, so switching plans starts clean.
 */
export function StepPlanView({
  planId,
  onChanged,
  onDeleted,
}: {
  planId: number;
  /** The plan changed: refresh the section's list. */
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const { plan, loading, loadError, actionError, dismissActionError, reload, mutate } = useStepPlan(
    planId,
    onChanged,
  );
  const [selected, setSelected] = useState<StepSelection>(null);
  const status = plan?.status;
  const stepIds = plan?.steps.map((s) => s.id).join(",");

  // A step that disappears (deleted, dismissed) drops out of the selection; the
  // first working step is picked the moment the plan starts.
  useEffect(() => {
    if (!plan) return;
    setSelected((current) => {
      if (typeof current === "number" && !plan.steps.some((s) => s.id === current)) return null;
      if (current === null && plan.status !== "drafting") return stepStartSelection(plan);
      return current;
    });
    // `plan` identity changes on every refetch; the ids and status are what matter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, stepIds]);

  if (loading && !plan) return <SectionLoading label="Loading step plan…" />;
  if (!plan) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
        <p className="max-w-sm text-sm text-red-500">{loadError ?? "This step plan could not be loaded."}</p>
        <button type="button" className={BTN_SECONDARY} onClick={() => void reload()}>
          Try again
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="step-plan-view">
      <StepPlanHeader
        plan={plan}
        mutate={mutate}
        onDeleted={onDeleted}
        onSelect={(selection) => {
          setSelected(selection);
          if (plan.archived_at && selection === "final") {
            document.getElementById("archived-plan-final")?.scrollIntoView({ behavior: "smooth" });
          }
        }}
      />
      {actionError && (
        <div className="border-b border-border px-5 py-2">
          <ErrorBanner message={actionError} onDismiss={dismissActionError} />
        </div>
      )}
      {plan.archived_at ? (
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <div className="mb-5 flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface p-3 text-sm">
            <p className="flex-1">This plan is archived. Restore it to resume or amend the work.</p>
            <button type="button" className={BTN_SECONDARY} onClick={() => void mutate(() => api.stepPlans.unarchive(plan.id))}>
              Restore plan
            </button>
          </div>
          <h2 className="mb-3 text-sm font-semibold">Steps</h2>
          <ol className="space-y-3">
            {flattenTree(buildStepTree(plan.steps)).map(({ step, depth }) => (
              <li key={step.id} className="rounded-lg border border-border p-3" style={{ marginLeft: depth * 16 }}>
                <div className="flex items-center gap-2">
                  <h3 className="min-w-0 flex-1 font-medium">{step.title}</h3>
                  <StepStatusPill step={step} />
                </div>
                {step.instruction && <p className="mt-1 text-sm text-muted">{step.instruction}</p>}
                {step.artifact && <Markdown className="mt-3">{step.artifact}</Markdown>}
              </li>
            ))}
          </ol>
          {plan.final_output && (
            <section id="archived-plan-final" className="mt-6">
              <h2 className="mb-3 text-sm font-semibold">Final output</h2>
              <Markdown>{plan.final_output}</Markdown>
            </section>
          )}
        </div>
      ) : plan.status === "drafting" ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <DraftingView
            plan={plan}
            mutate={mutate}
            selectedId={typeof selected === "number" ? selected : null}
            onSelect={(id) => setSelected(id)}
          />
        </div>
      ) : (
        <WorkingView plan={plan} mutate={mutate} reload={reload} selected={selected} onSelect={setSelected} />
      )}
    </div>
  );
}
