import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { subscribeAgentChanged } from "./events";
import { navigate } from "./routes";
import type { AgentRun } from "./types";

export type RunChoice = number | "all" | null;

export interface AgentRuns {
  runs: AgentRun[];
  /** The runs on hand belong to the agent on screen. */
  runsLoaded: boolean;
  runChoice: RunChoice;
  setRunChoice: (choice: RunChoice) => void;
  /** The run the transcript is scoped to; null shows every run. */
  runFilter: number | null;
  /** Drop an open message permalink (`#event=…`), keeping the all-runs view it showed. */
  leaveEventLink: () => void;
}

/**
 * An agent's executions, and which of them the transcript shows.
 *
 * A reusable agent driven by two workflows at once produces two conversations
 * in one archive; reading them interleaved is meaningless (issue #242). So the
 * transcript is scoped to one run: `runChoice` holds the user's *choice*, where
 * `null` follows the newest run — opening an agent shows what it just did
 * rather than every execution ever stitched together. Picking a run or "All
 * runs" pins the view until the user switches agent. A message permalink
 * (`linked`) shows every run, since the linked reply may be an earlier one.
 *
 * The runs are loaded here rather than inside the Runs rail because the
 * transcript defaults to the newest one — a collapsed insights panel must not
 * change what you read. They're tagged with the agent they belong to so
 * switching agents can't briefly scope the transcript to the previous one's run.
 */
export function useAgentRuns(agentId: number | null, linked: boolean): AgentRuns {
  const [runChoice, setRunChoice] = useState<RunChoice>(null);
  const [runsState, setRunsState] = useState<{ agentId: number | null; items: AgentRun[] }>({
    agentId: null,
    items: [],
  });

  useEffect(() => {
    if (agentId == null) {
      setRunsState({ agentId: null, items: [] });
      return;
    }
    const load = (): Promise<void> =>
      api.agents
        .runs(agentId, { limit: 12 })
        .then((items) => setRunsState({ agentId, items }))
        .catch(() => setRunsState({ agentId, items: [] }));
    void load();
    // A run opens and closes as the agent works, so the rail — and the default
    // filter riding on it — follow the live stream rather than a manual reload.
    return subscribeAgentChanged(agentId, load);
  }, [agentId]);

  // A pinned run belongs to the agent it was chosen on — switching agents must
  // not leave the next transcript scoped to a run that isn't even theirs, or
  // stuck on "all runs" when the default is to read the latest.
  useEffect(() => {
    setRunChoice(null);
  }, [agentId]);

  const runsLoaded = runsState.agentId === agentId;
  const runs = useMemo(() => (runsLoaded ? runsState.items : []), [runsLoaded, runsState.items]);
  // Left alone the filter tracks the newest run, so starting the agent again
  // pulls the view along instead of stranding you on a finished conversation.
  const latestRunId = runs[0]?.id ?? null;
  const runFilter = linked || runChoice === "all" ? null : (runChoice ?? latestRunId);
  const leaveEventLink = useCallback(() => {
    const url = new URL(window.location.href);
    const hash = new URLSearchParams(url.hash.slice(1));
    if (!hash.has("event")) return;
    hash.delete("event");
    // Keep the all-runs context when the user leaves a linked earlier reply.
    setRunChoice("all");
    navigate(url.pathname + url.search + (hash.size ? `#${hash}` : ""), { replace: true });
  }, []);
  return { runs, runsLoaded, runChoice, setRunChoice, runFilter, leaveEventLink };
}
