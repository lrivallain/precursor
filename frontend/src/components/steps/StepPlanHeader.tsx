import { useLayoutEffect, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowRight, Check, ClipboardCheck, FileCheck2, Lightbulb, Trash2, X } from "lucide-react";
import { api, apiErrorMessage } from "../../lib/api";
import { PLAN_STAGES, autoHasWork, planNext, planProgress, planStage } from "../../lib/stepPlans";
import { openTopic } from "../../lib/sideChats";
import type { StepPlan } from "../../lib/types";
import { useConfirm } from "../ConfirmDialog";
import { InlineTitle } from "../InlineTitle";
import { Markdown } from "../Markdown";
import { AutomationBar } from "./AutomationBar";
import { STEPS_HEADER_SLOT } from "./StepsSectionParts";
import {
  BTN_PRIMARY,
  ErrorBanner,
  OverflowMenu,
  PlanStatusPill,
  ProvisionalBadge,
  StaleBadge,
  type MenuItem,
} from "./StepUi";
import type { StepSelection } from "./WorkingView";

type Mutate = <T>(action: () => Promise<T>) => Promise<T | undefined>;

/** Where the plan is in its life, as a short trail: the current stage is bold, earlier ones are ticked. */
function StageTrail({ plan }: { plan: StepPlan }) {
  const current = planStage(plan);
  const at = PLAN_STAGES.findIndex((s) => s.id === current);
  return (
    <>
    <span
      className={`hidden shrink-0 text-xs font-semibold md:inline min-[1900px]:hidden ${
        current === "done" ? "text-emerald-700 dark:text-emerald-300" : "text-sky-700 dark:text-sky-300"
      }`}
    >
      {current === "done" ? "Done" : PLAN_STAGES[at]?.label}
    </span>
    <ol className="hidden shrink-0 items-center gap-1 text-xs min-[1900px]:flex" aria-label="Plan progress" data-testid="stage-trail">
      {PLAN_STAGES.map((stage, i) => {
        const past = i < at || current === "done";
        const active = i === at && current !== "done";
        return (
          <li
            key={stage.id}
            aria-current={active ? "step" : undefined}
            className={`flex items-center gap-1 ${
              active ? "font-semibold text-sky-700 dark:text-sky-300" : past ? "text-emerald-700 dark:text-emerald-300" : "text-muted"
            }`}
          >
            {i > 0 && <span className="text-muted/50">›</span>}
            {past && <Check size={11} aria-hidden />}
            {stage.label}
          </li>
        );
      })}
    </ol>
    </>
  );
}

/**
 * The plan's top bar: title, where it is in its life, the one thing to do
 * next, the automation mode, and a "Plan tools" menu for the rest.
 */
export function StepPlanHeader({
  plan,
  mutate,
  onDeleted,
  onSelect,
}: {
  plan: StepPlan;
  mutate: Mutate;
  onDeleted: () => void;
  onSelect: (selection: StepSelection) => void;
}) {
  const confirm = useConfirm();
  const [review, setReview] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const { done, total } = planProgress(plan);
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => setSlot(document.getElementById(STEPS_HEADER_SLOT)), []);
  const next = planNext(plan);
  const running = plan.auto_state === "running";

  async function runReview(): Promise<void> {
    setReviewError(null);
    try {
      setReview((await api.stepPlans.review(plan.id)).text);
    } catch (err) {
      setReviewError(apiErrorMessage(err, "The review failed."));
    }
  }

  async function remove(): Promise<void> {
    const ok = await confirm({
      title: "Delete this step plan?",
      message: `“${plan.title}” will be deleted with all its steps, artifacts, chats and final output. The source topic is not touched.`,
      confirmLabel: "Delete plan",
      variant: "danger",
    });
    if (!ok) return;
    const result = await mutate(async () => {
      await api.stepPlans.remove(plan.id);
      return true;
    });
    if (result) onDeleted();
  }

  const working = plan.status !== "drafting" && plan.status !== "cancelled";
  const tools: MenuItem[] = [
    {
      label: "Suggest missing steps",
      icon: Lightbulb,
      onSelect: async () => {
        await mutate(() => api.stepPlans.suggestMissing(plan.id));
      },
      disabledReason: plan.steps.length === 0 ? "Add steps first" : undefined,
    },
    {
      label: "Review the plan",
      icon: ClipboardCheck,
      onSelect: runReview,
      disabledReason: plan.steps.length === 0 ? "Add steps first" : undefined,
    },
    ...(working
      ? [{ label: "Open the final output", icon: FileCheck2, onSelect: () => onSelect("final") } satisfies MenuItem]
      : []),
    { label: "Delete plan", icon: Trash2, danger: true, separated: true, onSelect: remove },
  ];

  const bar = (
    <div className="flex min-w-0 flex-1 items-center gap-2" data-testid="plan-header">
      <InlineTitle
        title={plan.title}
        onRename={(title) => void mutate(() => api.stepPlans.update(plan.id, { title }))}
        className="min-w-0 flex-1 truncate font-medium"
        inputClassName="min-w-0 flex-1 rounded border border-accent/60 bg-bg px-1.5 py-0.5 font-medium outline-none"
      />
      {plan.status === "drafting" || plan.status === "cancelled" ? (
        <PlanStatusPill status={plan.status} />
      ) : (
        <StageTrail plan={plan} />
      )}
      {plan.final_provisional && (plan.final_output ?? "").trim() !== "" && <ProvisionalBadge />}
      {plan.final_stale && <StaleBadge />}
      {working && (
        <span className="hidden shrink-0 text-xs tabular-nums text-muted md:inline" data-tooltip="Steps approved">
          {done}/{total} approved
        </span>
      )}
      {plan.source_topic_id != null && (
        <button
          type="button"
          onClick={() => openTopic(plan.source_topic_id!)}
          className="hidden max-w-[14rem] shrink truncate rounded-full border border-accent/30 bg-accent/[0.06] px-2.5 py-0.5 text-xs text-accent underline-offset-2 hover:border-accent/60 hover:bg-accent/10 hover:underline lg:inline"
          data-tooltip={`Go to the topic this plan came from\n${plan.source_topic_title ?? ""}`}
        >
          {plan.source_topic_title ?? "Source topic"}
        </button>
      )}
      {plan.status !== "cancelled" && (
        <AutomationBar plan={plan} mutate={mutate} canRun={autoHasWork(plan)} />
      )}
      {next && !running && plan.status !== "done" && (
        <button
          type="button"
          className={BTN_PRIMARY}
          onClick={() => onSelect(next.target)}
          data-testid="plan-next"
          data-tooltip="The next thing to do on this plan"
        >
          {next.label} <ArrowRight size={14} />
        </button>
      )}
      <OverflowMenu items={tools} label="Plan tools" />
    </div>
  );

  const hasBody = reviewError !== null || review !== null;
  return (
    <>
      {slot ? createPortal(bar, slot) : <div className="border-b border-border px-5 py-3">{bar}</div>}
      {hasBody && (
        <div className="border-b border-border px-5 pb-3">
          {reviewError && (
            <div className="mt-3">
              <ErrorBanner message={reviewError} onDismiss={() => setReviewError(null)} />
            </div>
          )}
          {review !== null && (
            <section className="mt-3 rounded-xl border border-border bg-surface p-4" aria-label="Plan review">
              <div className="mb-2 flex items-center gap-2">
                <h3 className="flex-1 text-sm font-semibold">Plan review</h3>
                <button
                  type="button"
                  onClick={() => setReview(null)}
                  className="rounded p-1 text-muted hover:bg-bg hover:text-fg"
                  aria-label="Dismiss the review"
                  data-tooltip="Dismiss"
                >
                  <X size={14} />
                </button>
              </div>
              <div className="max-h-72 overflow-y-auto">
                <Markdown>{review}</Markdown>
              </div>
            </section>
          )}
        </div>
      )}
    </>
  );
}
