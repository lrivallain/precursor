/**
 * Shared agent presentation logic — the single source of truth for how an
 * agent's status is ranked, coloured and labelled across every surface (the
 * sidebar list, the dashboard cockpit, status badges, the tab title and the
 * out-of-band waiting signal).
 *
 * Centralising this here is what makes agents feel like one monitorable fleet
 * rather than scattered "alternative topics": an attention state looks and
 * sorts the same everywhere it appears.
 *
 * All colour values are full Tailwind class strings (never interpolated) so the
 * compiler keeps them at build time.
 */

import type { AgentSession, AgentStatus } from "./types";

// Human-readable status labels (used by badges, medallions and cards).
export const AGENT_STATUS_LABEL: Record<AgentStatus, string> = {
  pending: "Pending",
  waiting: "Waiting",
  running: "Running",
  idle: "Idle",
  needs_approval: "Needs approval",
  blocked: "Needs input",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
};

// Soft badge treatment (translucent fill + text) for inline status pills.
export const AGENT_STATUS_BADGE: Record<AgentStatus, string> = {
  pending: "bg-amber-500/15 text-amber-500",
  waiting: "bg-slate-500/15 text-slate-400",
  running: "bg-sky-500/15 text-sky-500",
  idle: "bg-emerald-500/15 text-emerald-500",
  needs_approval: "bg-orange-500/15 text-orange-500",
  blocked: "bg-amber-500/15 text-amber-500",
  completed: "bg-emerald-500/15 text-emerald-500",
  failed: "bg-red-500/15 text-red-500",
  cancelled: "bg-muted/20 text-muted",
  interrupted: "bg-red-500/15 text-red-500",
};

// Solid dot colour for the shared status medallion.
export const AGENT_STATUS_DOT: Record<AgentStatus, string> = {
  pending: "bg-amber-500",
  waiting: "bg-slate-400",
  running: "bg-sky-500",
  idle: "bg-emerald-500",
  needs_approval: "bg-orange-500",
  blocked: "bg-amber-500",
  completed: "bg-emerald-500",
  failed: "bg-red-500",
  cancelled: "bg-muted",
  interrupted: "bg-red-500",
};

// Attention ring colour, keyed to the same hue as the dot — only rendered for
// states that pull for the operator's eye.
export const AGENT_STATUS_RING: Record<AgentStatus, string> = {
  pending: "ring-amber-500/40",
  waiting: "ring-slate-400/40",
  running: "ring-sky-500/40",
  idle: "ring-emerald-500/40",
  needs_approval: "ring-orange-500/50",
  blocked: "ring-amber-500/50",
  completed: "ring-emerald-500/40",
  failed: "ring-red-500/50",
  cancelled: "ring-muted/30",
  interrupted: "ring-red-500/50",
};

// Ordering for the attention router: lower sorts first (most urgent on top).
// Mirrors Voyageur's urgency queue — an agent waiting on the human (a parked
// approval or a raised question) beats everything, then broken/interrupted
// work, then live work, then quiet states.
export const AGENT_URGENCY_RANK: Record<AgentStatus, number> = {
  needs_approval: 0,
  blocked: 1,
  interrupted: 2,
  failed: 3,
  running: 4,
  pending: 5,
  idle: 6,
  completed: 7,
  waiting: 8,
  cancelled: 9,
};

// Statuses that demand a human: they drive the amber medallion ring, the
// dashboard "needs attention" lane and the out-of-band notification.
const ATTENTION_STATUSES: ReadonlySet<AgentStatus> = new Set([
  "needs_approval",
  "blocked",
  "interrupted",
  "failed",
]);

export function agentNeedsAttention(agent: AgentSession): boolean {
  return ATTENTION_STATUSES.has(agent.status);
}

/** Whether the agent is doing work right now (drives the pulsing medallion). */
export function agentIsActive(agent: AgentSession): boolean {
  return agent.status === "running" || agent.status === "pending";
}

/**
 * Attention-router ordering: most urgent first, then unread replies, then most
 * recently active, then newest. Stable enough that the list/dashboard don't
 * jump around on every poll for agents in the same state.
 */
export function compareAgentsByUrgency(a: AgentSession, b: AgentSession): number {
  const rank = AGENT_URGENCY_RANK[a.status] - AGENT_URGENCY_RANK[b.status];
  if (rank !== 0) return rank;
  if (a.unread_count !== b.unread_count) return b.unread_count - a.unread_count;
  const ta = a.last_activity_at ? Date.parse(a.last_activity_at) : 0;
  const tb = b.last_activity_at ? Date.parse(b.last_activity_at) : 0;
  if (ta !== tb) return tb - ta;
  return Date.parse(b.created_at) - Date.parse(a.created_at);
}

/** Copy of the agents, urgency-sorted (never mutates the input array). */
export function sortAgentsByUrgency(agents: readonly AgentSession[]): AgentSession[] {
  return [...agents].sort(compareAgentsByUrgency);
}

/** Count of agents currently blocked on the human (drives tab-title + palette). */
export function agentsWaitingCount(agents: readonly AgentSession[]): number {
  return agents.reduce(
    (n, a) => (a.status === "needs_approval" || a.status === "blocked" ? n + 1 : n),
    0,
  );
}

/** Compact "just now / 5m ago / 3h ago / date" formatter for activity stamps. */
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
