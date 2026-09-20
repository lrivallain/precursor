import { useMemo } from "react";
import { AGENT_STATUS_DOT, AGENT_STATUS_LABEL, groupAgentsByWorkflow } from "../lib/agents";
import type { AgentSession } from "../lib/types";
import { SectionList } from "./SectionList";
import { WorkflowAgentFilter } from "./WorkflowAgentFilter";

interface Props {
  agents: AgentSession[];
  activeId: number | null;
  overviewSelected: boolean;
  loading: boolean;
  enabled: boolean;
  showWorkflowAgents: boolean;
  onShowWorkflowAgentsChange: (show: boolean) => void;
  error: string | null;
  onRetry: () => void;
  onOverview: () => void;
  onSelect: (id: number) => void;
}

export function AgentList({
  agents,
  enabled,
  showWorkflowAgents,
  onShowWorkflowAgentsChange,
  ...props
}: Props) {
  const items = useMemo(
    () => groupAgentsByWorkflow(agents, showWorkflowAgents).flatMap((group) =>
      group.agents.map((agent) => ({
        id: agent.id,
        label: agent.title,
        detail: AGENT_STATUS_LABEL[agent.status],
        dot: AGENT_STATUS_DOT[agent.status],
        unread: agent.unread_count,
        group: group.label,
      })),
    ),
    [agents, showWorkflowAgents],
  );
  return (
    <SectionList
      {...props}
      label="Agents"
      items={enabled ? items : []}
      controls={enabled && (
        <WorkflowAgentFilter checked={showWorkflowAgents} onChange={onShowWorkflowAgentsChange} />
      )}
      emptyMessage={!enabled
        ? "Enable Agents in Settings to get started."
        : agents.length > 0 && !showWorkflowAgents
          ? "No standalone agents. Show workflow agents to see the full fleet."
          : "No agents yet. Create one with New agent."}
    />
  );
}
