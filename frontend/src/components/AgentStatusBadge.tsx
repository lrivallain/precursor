import { Loader2 } from "lucide-react";
import type { AgentStatus } from "../lib/types";
import { AGENT_STATUS_BADGE, AGENT_STATUS_LABEL, agentRelativeTime } from "../lib/agents";

export function AgentStatusBadge({ status }: { status: AgentStatus }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium ${AGENT_STATUS_BADGE[status]}`}
    >
      {(status === "running" || status === "pending") && (
        <Loader2 size={10} className="animate-spin" />
      )}
      {AGENT_STATUS_LABEL[status]}
    </span>
  );
}

// Re-exported so existing importers keep working after the helper moved into
// the shared agents lib.
export { agentRelativeTime };
