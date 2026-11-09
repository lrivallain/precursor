import { useEffect, useMemo } from "react";
import { pendingLanded } from "./agentCockpitModel";
import type { AgentEvent } from "./types";

/**
 * Optimistic echo of a just-sent prompt, scoped to the agent it targets so it
 * survives the switch into a freshly created session but never leaks onto
 * another. Cleared once the backend records the real user turn.
 */
export interface PendingEcho {
  agentId: number;
  text: string;
}

/**
 * Whether the echo is on screen for `agentId`. Computed in render so the echo
 * is dropped in the same paint that shows the real node — no duplicate flashes
 * — while the effect clears the state.
 */
export function usePendingEcho(
  events: AgentEvent[],
  agentId: number,
  pending: PendingEcho | null,
  setPending: (pending: PendingEcho | null) => void,
): boolean {
  const landed = useMemo(() => (pending ? pendingLanded(events, pending.text) : false), [events, pending]);
  useEffect(() => {
    if (landed) setPending(null);
  }, [landed, setPending]);
  return pending != null && pending.agentId === agentId && !landed;
}
