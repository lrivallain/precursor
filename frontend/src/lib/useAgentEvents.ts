import { useCallback, useEffect, useRef, useState } from "react";
import { latestMcpAuth, mergeEventPage, sameOr, sameThinkingOr } from "./agentCockpitModel";
import { api } from "./api";
import { subscribeAgentChanged } from "./events";
import { mcpAuthStore } from "./mcpAuth";
import type { AgentEvent, AgentLiveThinking } from "./types";

export interface AgentEvents {
  events: AgentEvent[];
  /** The round's thinking as it streams (outside the cursor, replaced per read). */
  liveThinking: AgentLiveThinking | null;
  /** Runs whose prompts can be rewound (the current SDK session's). */
  rewindableRunIds: readonly number[];
  /** The transcript of this agent and run filter has been read at least once. */
  ready: boolean;
  /** Re-read the transcript from scratch (after a rewind). */
  reloadFresh: () => void;
}

const scopeKey = (id: number | null, runFilter: number | null): string =>
  `${id}:${runFilter ?? "all"}`;

/**
 * The selected agent's transcript, kept live on agent.changed.
 *
 * Incremental: `archived` is the append-only history the server has confirmed
 * and `cursor` how much of it we hold, so a refresh asks only for what arrived
 * since. Refs rather than state because a refresh reads them without wanting
 * to be re-subscribed when they move. `onLinkError` reports a failed read while
 * a message permalink is open.
 */
export function useAgentEvents(
  agentId: number | null,
  runFilter: number | null,
  onLinkError: (message: string) => void,
): AgentEvents {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [loadedKey, setLoadedKey] = useState("");
  const [liveThinking, setLiveThinking] = useState<AgentLiveThinking | null>(null);
  const [rewindableRunIds, setRewindableRunIds] = useState<readonly number[]>([]);
  const archivedRef = useRef<AgentEvent[]>([]);
  const scopeRef = useRef("");
  const cursorRef = useRef(0);
  // The archive the cursor counts into; a rewind changes it (see AgentEventPage).
  const epochRef = useRef<string | null>(null);

  const clear = useCallback(() => {
    archivedRef.current = [];
    cursorRef.current = 0;
    epochRef.current = null;
    setEvents([]);
    setLiveThinking(null);
    setRewindableRunIds([]);
  }, []);

  const load = useCallback(
    async (id: number, agentRunId: number | null, fresh = false): Promise<void> => {
      const key = scopeKey(id, agentRunId);
      try {
        const page = await api.agents.getEvents(id, agentRunId, fresh ? 0 : cursorRef.current, epochRef.current);
        if (scopeRef.current !== key) return;
        const archived = mergeEventPage(archivedRef.current, page, fresh);
        archivedRef.current = archived;
        cursorRef.current = page.cursor;
        epochRef.current = page.epoch ?? null;
        // Parked approval cards live outside the cursor and are replaced whole,
        // so they're re-appended on every read rather than accumulated.
        setEvents(page.pending.length > 0 ? [...archived, ...page.pending] : archived);
        setLoadedKey(key);
        setLiveThinking((prev) => sameThinkingOr(prev, page.thinking ?? null));
        setRewindableRunIds((prev) => sameOr(prev, page.rewindable_run_ids ?? []));
      } catch (reason) {
        if (scopeRef.current !== key) return;
        if (new URLSearchParams(window.location.hash.slice(1)).has("event")) {
          const detail = reason instanceof Error ? reason.message : String(reason);
          onLinkError(`Could not load this message: ${detail}`);
        }
        clear();
      }
    },
    [clear, onLinkError],
  );

  useEffect(() => {
    scopeRef.current = scopeKey(agentId, runFilter);
    setLoadedKey("");
    if (agentId == null) {
      archivedRef.current = [];
      cursorRef.current = 0;
      setEvents([]);
      setLiveThinking(null);
      return;
    }
    // A different agent or run filter is a different sequence, so a carried-over
    // cursor would address the wrong transcript: start this one from scratch.
    void load(agentId, runFilter, true);
    // Thereafter refresh incrementally — coalesced so a burst costs one read,
    // and cursored so that read carries only the steps since the last one.
    const unsubscribe = subscribeAgentChanged(agentId, () => load(agentId, runFilter));
    return () => {
      scopeRef.current = "";
      unsubscribe();
    };
  }, [agentId, load, runFilter]);

  // Drive the app-global sign-in banner when a turn surfaces an MCP server that
  // needs interactive auth (e.g. WorkIQ OAuth lapsed), so the user can
  // re-authenticate inline instead of digging through Settings.
  useEffect(() => {
    const auth = latestMcpAuth(events);
    if (auth) mcpAuthStore.report(auth.server, auth.message);
  }, [events]);

  const reloadFresh = useCallback(() => {
    if (agentId != null) void load(agentId, runFilter, true);
  }, [agentId, runFilter, load]);

  return {
    events,
    liveThinking,
    rewindableRunIds,
    ready: loadedKey === scopeKey(agentId, runFilter),
    reloadFresh,
  };
}
