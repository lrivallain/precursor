import { useMemo } from "react";
import { AGENT_STATUS_DOT, AGENT_STATUS_LABEL, sortAgentsByUrgency } from "../lib/agents";
import type { AgentSession } from "../lib/types";
import { SectionList } from "./SectionList";

interface Props {
  agents: AgentSession[];
  activeId: number | null;
  overviewSelected: boolean;
  loading: boolean;
  enabled: boolean;
  error: string | null;
  onRetry: () => void;
  onOverview: () => void;
  onSelect: (id: number) => void;
}

export function AgentList({ agents, enabled, ...props }: Props) {
  const items = useMemo(
    () => sortAgentsByUrgency(agents).map((agent) => ({
      id: agent.id,
      label: agent.title,
      detail: AGENT_STATUS_LABEL[agent.status],
      dot: AGENT_STATUS_DOT[agent.status],
      unread: agent.unread_count,
    })),
    [agents],
  );
  return (
    <SectionList
      {...props}
      label="Agents"
      items={enabled ? items : []}
      emptyMessage={enabled ? "No agents yet. Create one with New agent." : "Enable Agents in Settings to get started."}
    />
  );
}
