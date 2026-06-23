import { Loader2 } from "lucide-react";
import type { AgentStatus } from "../lib/types";

const STATUS_STYLES: Record<AgentStatus, string> = {
  pending: "bg-amber-500/15 text-amber-500",
  running: "bg-sky-500/15 text-sky-500",
  idle: "bg-emerald-500/15 text-emerald-500",
  needs_approval: "bg-orange-500/15 text-orange-500",
  completed: "bg-emerald-500/15 text-emerald-500",
  failed: "bg-red-500/15 text-red-500",
  cancelled: "bg-muted/20 text-muted",
  interrupted: "bg-red-500/15 text-red-500",
};

const STATUS_LABEL: Record<AgentStatus, string> = {
  pending: "Pending",
  running: "Running",
  idle: "Idle",
  needs_approval: "Needs approval",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
};

export function AgentStatusBadge({ status }: { status: AgentStatus }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium ${STATUS_STYLES[status]}`}
    >
      {(status === "running" || status === "pending") && (
        <Loader2 size={10} className="animate-spin" />
      )}
      {STATUS_LABEL[status]}
    </span>
  );
}

export function agentRelativeTime(iso: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  const mins = Math.round(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return new Date(iso).toLocaleDateString();
}
