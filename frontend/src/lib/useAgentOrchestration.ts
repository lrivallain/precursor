import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { subscribeAgentChanged } from "./events";
import type { AgentState, AgentTrigger } from "./types";

export interface AgentOrchestration {
  triggers: AgentTrigger[];
  state: AgentState[];
  busy: boolean;
  addWebhook: () => void;
  removeWebhook: (triggerId: number) => void;
  removeStateKey: (key: string) => void;
  resetState: () => void;
}

/**
 * An agent's durable cross-run state and its external webhook triggers, kept
 * live: a running agent saves state as it goes. Failed edits leave the lists
 * as they were.
 */
export function useAgentOrchestration(agentId: number): AgentOrchestration {
  const [triggers, setTriggers] = useState<AgentTrigger[]>([]);
  const [state, setState] = useState<AgentState[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    (): Promise<void> =>
      // Awaited as a unit so the coalesced refresh treats both as one in-flight
      // round-trip and won't start another until they land.
      Promise.all([
        api.agents.listTriggers(agentId).then(setTriggers).catch(() => setTriggers([])),
        api.agents.listState(agentId).then(setState).catch(() => setState([])),
      ]).then(() => undefined),
    [agentId],
  );

  useEffect(() => {
    void load();
    // Coalesced, since a running turn signals at token cadence.
    return subscribeAgentChanged(agentId, load);
  }, [load, agentId]);

  const edit = useCallback((fn: () => Promise<void>) => {
    setBusy(true);
    void fn()
      .catch(() => undefined)
      .finally(() => setBusy(false));
  }, []);

  return {
    triggers,
    state,
    busy,
    addWebhook: () =>
      edit(async () => {
        const t = await api.agents.createTrigger(agentId, { type: "webhook" });
        setTriggers((prev) => [...prev, t]);
      }),
    removeWebhook: (triggerId) =>
      edit(async () => {
        await api.agents.deleteTrigger(agentId, triggerId);
        setTriggers((prev) => prev.filter((t) => t.id !== triggerId));
      }),
    removeStateKey: (key) =>
      edit(async () => {
        await api.agents.deleteState(agentId, key);
        setState((prev) => prev.filter((s) => s.key !== key));
      }),
    resetState: () =>
      edit(async () => {
        await api.agents.clearState(agentId);
        setState([]);
      }),
  };
}
