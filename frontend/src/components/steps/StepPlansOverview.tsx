import { ListChecks, Plus } from "lucide-react";
import type { StepPlanItem } from "../../lib/types";
import { PLAN_STATUS_LABEL, PLAN_STATUS_PILL } from "../../lib/stepPlans";
import { SectionLoading } from "../SectionLoading";
import { BTN_PRIMARY, StaleBadge } from "./StepUi";

/** The section's landing page: every plan as a card, newest activity first. */
export function StepPlansOverview({
  plans,
  loading,
  error,
  onOpen,
  onNew,
  onRetry,
}: {
  plans: StepPlanItem[];
  loading: boolean;
  error: string | null;
  onOpen: (id: number) => void;
  onNew: () => void;
  onRetry: () => void;
}) {
  if (loading && plans.length === 0) return <SectionLoading label="Loading step plans…" />;
  return (
    <div className="mx-auto h-full max-w-4xl overflow-y-auto p-6">
      <div className="mb-5 flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold">Step by step</h1>
          <p className="mt-1 text-sm text-muted">
            Split a long ask into steps, work each one with its own artifact and chat, then
            consolidate everything into a single answer.
          </p>
        </div>
        <button type="button" onClick={onNew} className={BTN_PRIMARY}>
          <Plus size={14} /> New step plan
        </button>
      </div>
      {error && (
        <p className="mb-4 text-sm text-red-500" role="alert">
          {error}{" "}
          <button type="button" className="underline" onClick={onRetry}>
            Retry
          </button>
        </p>
      )}
      {plans.length === 0 && !error ? (
        <div className="rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted">
          <ListChecks size={28} className="mx-auto mb-3 opacity-60" />
          <p>No step plans yet.</p>
          <p className="mt-1">
            Use <code className="rounded bg-surface px-1">/steps</code> in a topic, or start from
            pasted text.
          </p>
        </div>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {plans.map((plan) => {
            const pct = plan.step_count > 0 ? Math.round((plan.validated_count / plan.step_count) * 100) : 0;
            return (
              <li key={plan.id}>
                <button
                  type="button"
                  onClick={() => onOpen(plan.id)}
                  className="flex h-full w-full flex-col gap-2 rounded-xl border border-border bg-surface/40 p-4 text-left hover:border-accent/50 hover:bg-surface"
                >
                  <span className="flex items-start gap-2">
                    <span className="min-w-0 flex-1 truncate font-medium">{plan.title}</span>
                    <span
                      className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${PLAN_STATUS_PILL[plan.status]}`}
                    >
                      {PLAN_STATUS_LABEL[plan.status]}
                    </span>
                  </span>
                  {plan.step_count > 0 && (
                    <span className="block">
                      <span className="block h-1.5 overflow-hidden rounded-full bg-border">
                        <span className="block h-full bg-teal-500" style={{ width: `${pct}%` }} />
                      </span>
                      <span className="mt-1 block text-xs text-muted">
                        {plan.validated_count}/{plan.step_count} approved
                      </span>
                    </span>
                  )}
                  <span className="flex flex-wrap items-center gap-1.5">
                    {plan.source_topic_title && (
                      <span className="max-w-full truncate rounded-full border border-border px-2 py-0.5 text-[11px] text-muted">
                        {plan.source_topic_title}
                      </span>
                    )}
                    {plan.final_stale && <StaleBadge />}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
