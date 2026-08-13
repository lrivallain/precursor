/**
 * The shared status medallion — one visual token for an agent's live state,
 * reused by the sidebar list, the dashboard cockpit and anywhere else an agent
 * is represented. Copied from Voyageur's orbital medallions: a status-coloured
 * dot that pulses while working, gains an amber ring when it's blocked on the
 * human, and grows a small satellite cluster when it fans out into parallel
 * tool calls (its "sub-agents").
 */

import type { AgentStatus } from "../lib/types";
import {
  AGENT_STATUS_DOT,
  AGENT_STATUS_LABEL,
  AGENT_STATUS_RING,
  agentNeedsAttention,
} from "../lib/agents";

interface AgentMedallionProps {
  status: AgentStatus;
  /** Tool calls running in parallel — shows a fan-out cluster when > 1. */
  activeToolCount?: number;
  /** Dot diameter in px (default 10). */
  size?: number;
  className?: string;
  title?: string;
}

export function AgentMedallion({
  status,
  activeToolCount = 0,
  size = 10,
  className = "",
  title,
}: AgentMedallionProps) {
  const active = status === "running" || status === "pending";
  const attention = agentNeedsAttention({ status } as never);
  const cluster = Math.min(activeToolCount, 9);
  return (
    <span
      className={`relative inline-flex shrink-0 items-center justify-center ${className}`}
      style={{ width: size, height: size }}
      title={title ?? AGENT_STATUS_LABEL[status]}
      aria-label={title ?? AGENT_STATUS_LABEL[status]}
    >
      {/* Pulsing halo while the agent is actively working. */}
      {active && (
        <span
          className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 ${AGENT_STATUS_DOT[status]}`}
        />
      )}
      <span
        className={`relative inline-flex rounded-full ${AGENT_STATUS_DOT[status]} ${
          attention ? `ring-2 ${AGENT_STATUS_RING[status]}` : ""
        }`}
        style={{ width: size, height: size }}
      />
      {/* Sub-agent fan-out cluster: a small count of parallel tool calls. */}
      {cluster > 1 && (
        <span className="absolute -right-1.5 -top-1.5 inline-flex min-w-[13px] items-center justify-center rounded-full bg-violet-500 px-[3px] text-[8px] font-bold leading-[13px] text-white shadow-sm">
          {cluster}
        </span>
      )}
    </span>
  );
}
