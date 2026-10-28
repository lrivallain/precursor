import { useCallback, useEffect, useState } from "react";
import { ListChecks, Loader2 } from "lucide-react";
import { api } from "../../lib/api";
import { eventBus } from "../../lib/events";
import { openStepPlan } from "../../lib/stepPlans";
import type { StepPlanItem } from "../../lib/types";
import { relativeWhen } from "../ChatStatsReminder";
import { PlanStatusPill } from "./StepUi";

export interface TopicStepPlansControls {
  items: StepPlanItem[];
  /** Split the topic's latest ask into a new plan. */
  onStart: () => void;
  starting: boolean;
}

/** The step plans spawned from a topic, kept current from `step-plan.changed`. */
export function useTopicStepPlans(topicId: number): StepPlanItem[] {
  const [items, setItems] = useState<StepPlanItem[]>([]);

  const load = useCallback(async () => {
    try {
      setItems(await api.stepPlans.list(topicId));
    } catch {
      // non-fatal: the section just keeps its last list
    }
  }, [topicId]);

  useEffect(() => {
    setItems([]);
    void load();
  }, [load]);

  useEffect(() => {
    eventBus.start();
    let timer: number | undefined;
    const off = eventBus.subscribe((event) => {
      if (event.type !== "step-plan.changed" && event.type !== "topic.changed") return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => void load(), 300);
    });
    return () => {
      off();
      window.clearTimeout(timer);
    };
  }, [load]);

  return items;
}

/** Sidebar section listing a topic's step plans, like its side chats. */
export function TopicStepPlansSection({ items, onStart, starting }: TopicStepPlansControls) {
  return (
    <div data-testid="topic-step-plans">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-muted">Step by step</span>
        <button
          type="button"
          onClick={onStart}
          disabled={starting}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-accent hover:bg-accent/10 disabled:opacity-50"
          data-tooltip={"Split the latest ask into steps\nThe model proposes the first steps"}
        >
          {starting ? <Loader2 size={12} className="animate-spin" /> : <ListChecks size={12} />}
          New
        </button>
      </div>
      {items.length === 0 ? (
        <p className="text-xs text-muted">None yet. Split the latest ask into steps from here.</p>
      ) : (
        <ul className="space-y-1">
          {items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => openStepPlan(item.id)}
                className="w-full rounded-md border border-border bg-surface/60 px-2 py-1.5 text-left hover:border-accent/50 hover:bg-surface"
              >
                <div className="flex items-center gap-1.5">
                  <ListChecks size={12} className="shrink-0 text-muted" aria-hidden />
                  <span className="min-w-0 flex-1 truncate">{item.title}</span>
                  <PlanStatusPill status={item.status} />
                </div>
                <div className="mt-0.5 flex justify-between gap-2 pl-[18px] text-[11px] text-muted tabular-nums">
                  <span>
                    {item.validated_count}/{item.step_count} validated
                  </span>
                  <span className="truncate">{relativeWhen(new Date(item.updated_at))}</span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
