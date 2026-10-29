import { useMemo } from "react";
import type { StepPlanItem } from "../../lib/types";
import { PLAN_STATUS_DOT, PLAN_STATUS_LABEL } from "../../lib/stepPlans";
import { SectionList } from "../SectionList";
import { StaleBadge } from "./StepUi";

interface Props {
  plans: StepPlanItem[];
  activeId: number | null;
  overviewSelected: boolean;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onOverview: () => void;
  onSelect: (id: number) => void;
}

export function StepPlanList({ plans, ...props }: Props) {
  const items = useMemo(
    () =>
      plans.map((plan) => ({
        id: plan.id,
        label: plan.title,
        detail:
          plan.status === "drafting"
            ? PLAN_STATUS_LABEL[plan.status]
            : `${PLAN_STATUS_LABEL[plan.status]} · ${plan.validated_count}/${plan.step_count} approved`,
        dot: PLAN_STATUS_DOT[plan.status],
        extra:
          plan.source_topic_title || plan.final_stale ? (
            <span className="mt-1 flex flex-wrap items-center gap-1">
              {plan.source_topic_title && (
                <span className="max-w-full truncate rounded-full border border-border px-1.5 py-px text-[10px] text-muted">
                  {plan.source_topic_title}
                </span>
              )}
              {plan.final_stale && <StaleBadge />}
            </span>
          ) : undefined,
      })),
    [plans],
  );
  return (
    <SectionList
      {...props}
      label="Step plans"
      items={items}
      emptyMessage="No step plans yet. Create one with New step plan."
    />
  );
}
