import { useState } from "react";
import { ClipboardCheck, Loader2, Trash2, X } from "lucide-react";
import { api, apiErrorMessage } from "../../lib/api";
import { planProgress } from "../../lib/stepPlans";
import { openTopic } from "../../lib/sideChats";
import type { StepPlan } from "../../lib/types";
import { useConfirm } from "../ConfirmDialog";
import { InlineTitle } from "../InlineTitle";
import { Markdown } from "../Markdown";
import { BTN_SECONDARY, ErrorBanner, PlanStatusPill, StaleBadge } from "./StepUi";

type Mutate = <T>(action: () => Promise<T>) => Promise<T | undefined>;

/** Plan title, status, source topic, progress, and the plan-level actions (review, delete). */
export function StepPlanHeader({
  plan,
  mutate,
  onDeleted,
}: {
  plan: StepPlan;
  mutate: Mutate;
  onDeleted: () => void;
}) {
  const confirm = useConfirm();
  const [review, setReview] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const { done, total } = planProgress(plan);

  async function runReview(): Promise<void> {
    setReviewing(true);
    setReviewError(null);
    try {
      setReview((await api.stepPlans.review(plan.id)).text);
    } catch (err) {
      setReviewError(apiErrorMessage(err, "The review failed."));
    } finally {
      setReviewing(false);
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

  return (
    <div className="border-b border-border px-5 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <InlineTitle
          title={plan.title}
          onRename={(title) => void mutate(() => api.stepPlans.update(plan.id, { title }))}
          className="min-w-0 max-w-full truncate text-lg font-semibold"
          inputClassName="min-w-0 flex-1 rounded border border-accent/60 bg-bg px-1.5 py-0.5 text-lg font-semibold outline-none"
        />
        <PlanStatusPill status={plan.status} />
        {plan.final_stale && <StaleBadge />}
        {plan.status !== "drafting" && (
          <span className="text-xs tabular-nums text-muted" data-tooltip="Working steps validated">
            {done}/{total} validated
          </span>
        )}
        {plan.source_topic_id != null && (
          <button
            type="button"
            onClick={() => openTopic(plan.source_topic_id!)}
            className="max-w-[16rem] truncate rounded-full border border-accent/30 bg-accent/[0.06] px-2.5 py-0.5 text-xs text-accent underline-offset-2 hover:border-accent/60 hover:bg-accent/10 hover:underline"
            data-tooltip={`Go to the topic this plan came from\n${plan.source_topic_title ?? ""}`}
          >
            {plan.source_topic_title ?? "Source topic"}
          </button>
        )}
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={() => void runReview()}
            disabled={reviewing || plan.steps.length === 0}
            className={BTN_SECONDARY}
            data-tooltip={
              plan.steps.length === 0
                ? "Add steps first"
                : "Ask the model whether the plan covers the whole ask"
            }
          >
            {reviewing ? <Loader2 size={14} className="animate-spin" /> : <ClipboardCheck size={14} />}
            Review plan
          </button>
          <button
            type="button"
            onClick={() => void remove()}
            className="rounded-lg p-2 text-muted hover:bg-red-500/10 hover:text-red-500"
            aria-label="Delete plan"
            data-tooltip="Delete this plan"
          >
            <Trash2 size={16} />
          </button>
        </div>
      </div>

      {reviewError && (
        <div className="mt-3">
          <ErrorBanner message={reviewError} onDismiss={() => setReviewError(null)} />
        </div>
      )}
      {review !== null && (
        <section
          className="mt-3 rounded-xl border border-border bg-surface p-4"
          aria-label="Plan review"
        >
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
  );
}
