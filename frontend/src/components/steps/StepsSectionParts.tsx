import type { StepPlansController } from "../../lib/useStepPlansController";
import { NewStepPlanDialog } from "./NewStepPlanDialog";
import { StepPlanList } from "./StepPlanList";
import { StepPlanView } from "./StepPlanView";
import { StepPlansOverview } from "./StepPlansOverview";

// The step-by-step section's pieces of the app shell: header, main pane and
// sidebar slot, all reading the section controller.

export const STEPS_HEADER_SLOT = "steps-header-slot";

// With a plan open, the app top bar is the plan's header (StepPlanHeader
// portals into this slot) so no second row is spent on it.
export function StepsHeader({ controller }: { controller: StepPlansController }) {
  if (controller.activePlanId == null) {
    return <span className="truncate font-medium min-w-0 flex-1">Step by step</span>;
  }
  return <div id={STEPS_HEADER_SLOT} className="flex min-w-0 flex-1 items-center gap-2" />;
}

export function StepsMain({ controller }: { controller: StepPlansController }) {
  const { activePlanId, plans, loading, error, reload, openPlan, newDialogOpen, setNewDialogOpen } =
    controller;
  return (
    <>
      {activePlanId != null ? (
        <StepPlanView
          key={activePlanId}
          planId={activePlanId}
          onChanged={reload}
          onDeleted={() => {
            reload();
            void openPlan(null);
          }}
        />
      ) : (
        <StepPlansOverview
          plans={plans}
          loading={loading}
          error={error}
          onOpen={(id) => void openPlan(id)}
          onNew={() => setNewDialogOpen(true)}
          onRetry={reload}
        />
      )}
      {newDialogOpen && (
        <NewStepPlanDialog
          onClose={() => setNewDialogOpen(false)}
          onCreated={(plan) => {
            setNewDialogOpen(false);
            void openPlan(plan.id);
          }}
          onFailed={reload}
        />
      )}
    </>
  );
}

export function StepsSidebarSlot({
  controller,
  onOverview,
}: {
  controller: StepPlansController;
  onOverview: () => void;
}) {
  const { plans, activePlanId, loading, error, reload, openPlan } = controller;
  return (
    <StepPlanList
      plans={plans}
      activeId={activePlanId}
      overviewSelected={activePlanId == null}
      loading={loading}
      error={error}
      onRetry={reload}
      onOverview={onOverview}
      onSelect={(id) => void openPlan(id)}
    />
  );
}
