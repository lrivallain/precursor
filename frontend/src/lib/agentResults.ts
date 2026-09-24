/**
 * Result versions for the agent cockpit.
 *
 * A follow-up that asks an agent to refine its deliverable publishes a new
 * artifact next to the old one — the blackboard keeps every output of a run
 * until the run starts over. Read as a flat list, those outputs stack up with
 * the newest buried among its predecessors. Grouped by the exchange (user
 * prompt) that produced them, they read as numbered versions of one result:
 * the latest is v-N, and each earlier one is still reachable with the prompt
 * that led to the next.
 */
import type { AgentArtifact } from "./types";

// The prefix of `_CONTINUE_NUDGE` in precursor/backend/services/agents/directives.py.
// The goal loop sends it as a user turn to keep an autonomous agent going, so
// it lands in the transcript looking like the human spoke — but it continues
// the current exchange rather than starting a new one.
const AUTONOMY_NUDGE_PREFIX = "Continue working autonomously toward your objective";

export function isAutonomyNudge(text: string | null | undefined): boolean {
  return (text ?? "").trimStart().startsWith(AUTONOMY_NUDGE_PREFIX);
}

/** One human prompt and what the agent did about it, as far as results care. */
export interface ExchangeAnchor {
  /** Ordinal of the exchange in the transcript (0-based). */
  index: number;
  /** When the prompt was recorded. */
  at: string | null;
  /** The prompt text. */
  prompt: string | null;
  /** The agent's own one-line account of the turn (`OBJECTIVE_COMPLETE:`). */
  summary: string | null;
}

/** The outputs one exchange published, numbered in publication order. */
export interface ResultVersion {
  /** 1-based version number. */
  n: number;
  exchange: ExchangeAnchor | null;
  artifacts: AgentArtifact[];
  /** When its newest artifact was published. */
  at: string;
}

function ms(at: string | null | undefined): number {
  if (!at) return Number.NaN;
  return Date.parse(at);
}

/**
 * The artifacts that make up the agent's result. The model's explicit
 * `ARTIFACT:` outputs are the deliverable; the auto-captured completion summary
 * (`key === "result"`) repeats the final answer, so it only stands in when
 * nothing explicit was published.
 */
export function resultArtifacts(
  artifacts: AgentArtifact[],
  runFilter: number | null,
): AgentArtifact[] {
  // Artifacts are run-scoped; unattributed rows belong to the agent as a whole.
  const scoped =
    runFilter == null
      ? artifacts
      : artifacts.filter((a) => a.agent_run_id == null || a.agent_run_id === runFilter);
  const outputs = scoped.filter((a) => a.key !== "result");
  const chosen = outputs.length > 0 ? outputs : scoped.filter((a) => a.key === "result");
  return [...chosen].sort((a, b) => ms(a.created_at) - ms(b.created_at) || a.id - b.id);
}

/**
 * Group the result artifacts into versions, one per exchange that published.
 *
 * An artifact belongs to the latest exchange whose prompt precedes it. Without
 * any prompt to anchor to (a cleared transcript, an API-published row) each
 * artifact stands as a version of its own.
 */
export function buildResultVersions(
  artifacts: AgentArtifact[],
  exchanges: ExchangeAnchor[],
  runFilter: number | null,
): ResultVersion[] {
  const anchored = exchanges
    .filter((e) => !Number.isNaN(ms(e.at)))
    .sort((a, b) => ms(a.at) - ms(b.at));
  const versions: ResultVersion[] = [];
  let current: ResultVersion | null = null;
  for (const art of resultArtifacts(artifacts, runFilter)) {
    const t = ms(art.created_at);
    let exchange: ExchangeAnchor | null = null;
    for (const e of anchored) {
      if (ms(e.at) <= t) exchange = e;
      else break;
    }
    if (current && exchange && current.exchange?.index === exchange.index) {
      current.artifacts.push(art);
      current.at = art.created_at;
      continue;
    }
    current = { n: versions.length + 1, exchange, artifacts: [art], at: art.created_at };
    versions.push(current);
  }
  return versions;
}

/**
 * Find the persisted artifact a message's `ARTIFACT:` directive published.
 *
 * The backend writes it when the turn settles, so it's the first artifact with
 * the same title created at or after the message.
 */
export function findPublished(
  versions: ResultVersion[],
  title: string,
  at: string | null,
): { version: ResultVersion; artifact: AgentArtifact } | null {
  const wanted = title.trim().slice(0, 200);
  const from = ms(at);
  let best: { version: ResultVersion; artifact: AgentArtifact } | null = null;
  for (const version of versions) {
    for (const artifact of version.artifacts) {
      if (artifact.title.trim() !== wanted) continue;
      const t = ms(artifact.created_at);
      // A little slack: the event and the row are stamped by different writers.
      if (!Number.isNaN(from) && t < from - 5000) continue;
      if (!best || t < ms(best.artifact.created_at)) best = { version, artifact };
    }
  }
  return best;
}
