import { useCallback, useEffect, useMemo, useState } from "react";
import type { AgentPane } from "../components/AgentResults";
import { resolvePane } from "./agentCockpitModel";
import { buildResultVersions, type ExchangeAnchor, type ResultVersion } from "./agentResults";
import { api } from "./api";
import { subscribeAgentChanged } from "./events";
import { navigate } from "./routes";
import type { AgentArtifact } from "./types";

/**
 * The agent's published artifacts: the Result tab's versions and the insights
 * sidebar's index. Tagged with their agent, like the runs, so switching agents
 * can't briefly show the previous one's result.
 */
export function useAgentArtifacts(agentId: number | null): { artifacts: AgentArtifact[]; loaded: boolean } {
  const [state, setState] = useState<{ agentId: number | null; items: AgentArtifact[]; loaded: boolean }>({
    agentId: null,
    items: [],
    loaded: false,
  });
  useEffect(() => {
    if (agentId == null) {
      setState({ agentId: null, items: [], loaded: false });
      return;
    }
    let alive = true;
    const load = (): Promise<void> =>
      api.agents
        .listArtifacts(agentId)
        .then((items) => {
          if (alive) setState({ agentId, items, loaded: true });
        })
        .catch(() => {
          if (alive) setState({ agentId, items: [], loaded: true });
        });
    void load();
    // A completed turn publishes its result, and mid-mission ARTIFACT
    // directives publish as they're emitted.
    const off = subscribeAgentChanged(agentId, load);
    return () => {
      alive = false;
      off();
    };
  }, [agentId]);
  const artifacts = useMemo(() => (state.agentId === agentId ? state.items : []), [state, agentId]);
  return { artifacts, loaded: state.agentId === agentId && state.loaded };
}

export interface AgentResultsOptions {
  agentId: number | null;
  runFilter: number | null;
  /** The runs are loaded, so the run filter is settled. */
  runsLoaded: boolean;
  anchors: ExchangeAnchor[];
  turnActive: boolean;
  atRest: boolean;
  needsYou: boolean;
  showPending: boolean;
  /** A message permalink is open. */
  linked: boolean;
  /** Leave a message permalink before showing something else. */
  leaveEventLink: () => void;
}

export interface AgentResults {
  artifacts: AgentArtifact[];
  versions: ResultVersion[];
  latestVersion: ResultVersion | null;
  /** The version on screen in the Result pane. */
  shownVersion: ResultVersion | null;
  pane: AgentPane;
  setPaneChoice: (pane: AgentPane | null) => void;
  /** Hand the pane back to the agent: a new turn is starting. */
  followTurn: () => void;
  /** An artifact that isn't a result version, open in a viewer over the page. */
  viewing: AgentArtifact | null;
  closeViewer: () => void;
  openVersion: (v: ResultVersion | null) => void;
  openArtifact: (a: AgentArtifact) => void;
  /** The result version an artifact belongs to ("v2"), if it's one. */
  artifactLabel: (a: AgentArtifact) => string | null;
}

// Open an artifact from a `?artifact={id}` permalink once the list has loaded,
// then strip the param so it doesn't re-fire on later reloads.
function useArtifactPermalink(
  artifacts: AgentArtifact[],
  ready: boolean,
  openArtifact: (a: AgentArtifact) => void,
): void {
  useEffect(() => {
    if (!ready || artifacts.length === 0) return;
    const raw = new URLSearchParams(window.location.search).get("artifact");
    if (!raw) return;
    const target = artifacts.find((a) => String(a.id) === raw);
    if (target) openArtifact(target);
    const url = new URL(window.location.href);
    url.searchParams.delete("artifact");
    navigate(url.pathname + url.search + url.hash, { replace: true });
  }, [ready, artifacts, openArtifact]);
}

/**
 * The agent's result as numbered versions (one per exchange that published),
 * and which pane — Result or Activity — is on screen.
 *
 * Left alone the pane follows the agent (see `resolvePane`). A click pins the
 * choice until the next turn starts or the agent needs you.
 */
export function useAgentResults(opts: AgentResultsOptions): AgentResults {
  const { agentId, runFilter, anchors, turnActive, needsYou, leaveEventLink } = opts;
  const { artifacts, loaded } = useAgentArtifacts(agentId);
  const versions = useMemo(
    () => buildResultVersions(artifacts, anchors, runFilter),
    [artifacts, anchors, runFilter],
  );
  const latestVersion = versions.length > 0 ? versions[versions.length - 1] : null;
  const versionOfArtifact = useMemo(() => {
    const map = new Map<number, ResultVersion>();
    for (const v of versions) for (const a of v.artifacts) map.set(a.id, v);
    return map;
  }, [versions]);
  // The version on screen, remembered by one of its artifacts so a regrouping
  // (the transcript loading after the artifacts) can't renumber it away. Null
  // follows the newest.
  const [versionPick, setVersionPick] = useState<number | null>(null);
  const [paneChoice, setPaneChoice] = useState<AgentPane | null>(null);
  const [viewing, setViewing] = useState<AgentArtifact | null>(null);
  const shownVersion =
    (versionPick != null ? versionOfArtifact.get(versionPick) : undefined) ?? latestVersion;
  const pane = resolvePane({
    choice: paneChoice,
    latestVersion,
    anchors,
    atRest: opts.atRest,
    showPending: opts.showPending,
    linked: opts.linked,
  });

  useEffect(() => {
    setPaneChoice(null);
    setVersionPick(null);
    setViewing(null);
  }, [agentId]);
  // Another run is another set of versions.
  useEffect(() => {
    setVersionPick(null);
  }, [agentId, runFilter]);
  // A new turn hands the pane back to the follow rule — its live steps, then
  // the newest version it concludes with, not one picked before it started.
  const followTurn = useCallback(() => {
    setPaneChoice(null);
    setVersionPick(null);
  }, []);
  useEffect(() => {
    if (turnActive) followTurn();
  }, [turnActive, followTurn]);
  // So does the agent stopping on you, even mid-turn (running → needs approval).
  useEffect(() => {
    if (needsYou) setPaneChoice(null);
  }, [needsYou]);

  const openVersion = useCallback(
    (v: ResultVersion | null) => {
      leaveEventLink();
      setVersionPick(v == null || v === latestVersion ? null : v.artifacts[0].id);
      setPaneChoice("result");
    },
    [latestVersion, leaveEventLink],
  );
  const openArtifact = useCallback(
    (a: AgentArtifact) => {
      const v = versionOfArtifact.get(a.id);
      if (v) openVersion(v);
      else setViewing(a);
    },
    [versionOfArtifact, openVersion],
  );
  const artifactLabel = useCallback(
    (a: AgentArtifact) => {
      const v = versionOfArtifact.get(a.id);
      return v ? `v${v.n}` : null;
    },
    [versionOfArtifact],
  );
  const closeViewer = useCallback(() => setViewing(null), []);
  // Wait for the runs too: until they land the run filter isn't settled, and an
  // artifact of an earlier run would pass for a version of the latest.
  useArtifactPermalink(artifacts, loaded && opts.runsLoaded, openArtifact);

  return {
    artifacts,
    versions,
    latestVersion,
    shownVersion,
    pane,
    setPaneChoice,
    followTurn,
    viewing,
    closeViewer,
    openVersion,
    openArtifact,
    artifactLabel,
  };
}
