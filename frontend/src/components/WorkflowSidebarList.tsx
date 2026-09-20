import { useMemo } from "react";
import type { Workflow } from "../lib/types";
import { WORKFLOW_STATUS_DOT, WORKFLOW_STATUS_LABEL, workflowProgress } from "../lib/workflows";
import { SectionList } from "./SectionList";

interface Props {
  workflows: Workflow[];
  activeId: number | null;
  overviewSelected: boolean;
  loading: boolean;
  enabled: boolean;
  error: string | null;
  onRetry: () => void;
  onOverview: () => void;
  onSelect: (id: number) => void;
}

export function WorkflowSidebarList({ workflows, enabled, ...props }: Props) {
  const items = useMemo(
    () => workflows.map((workflow) => {
      const progress = workflowProgress(workflow);
      return {
        id: workflow.id,
        label: workflow.name,
        detail: `${WORKFLOW_STATUS_LABEL[workflow.status]}${progress?.live ? ` \u00b7 ${progress.pct}%` : ""}`,
        dot: WORKFLOW_STATUS_DOT[workflow.status],
      };
    }),
    [workflows],
  );
  return (
    <SectionList
      {...props}
      label="Workflows"
      items={enabled ? items : []}
      emptyMessage={enabled ? "No workflows yet. Create one with New workflow." : "Enable Agents in Settings to use workflows."}
    />
  );
}
