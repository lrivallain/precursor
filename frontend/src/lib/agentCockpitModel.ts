// Pure helpers behind the agent cockpit (components/agent/): status, the
// transcript page merge, turn grouping and pane choice.
import { classify } from "../components/AgentTimeline";
import type { AgentPane } from "../components/AgentResults";
import { isAutonomyNudge, type ExchangeAnchor, type ResultVersion } from "./agentResults";
import type { AgentTurn } from "./useAgentRewind";
import type { AgentEvent, AgentEventPage, AgentLiveThinking, AgentRun, AgentSession } from "./types";

// Short label for what set a run going. Kept terse — it sits in a 60-wide rail.
export const RUN_TRIGGER_LABEL: Record<AgentRun["trigger"], string> = {
  manual: "Manual",
  workflow: "Workflow",
  schedule: "Schedule",
  webhook: "Webhook",
  fleet: "Fleet",
  retry: "Retry",
  replay: "Replay",
};

/** "Run #12 · Workflow · workflow run #3". */
export function describeRun(run: AgentRun): string {
  const workflow = run.workflow_run_id ? ` · workflow run #${run.workflow_run_id}` : "";
  return `Run #${run.id} · ${RUN_TRIGGER_LABEL[run.trigger]}${workflow}`;
}

export interface AgentStatusFlags {
  /** A turn is in flight (or about to be). */
  turnActive: boolean;
  /** The agent is at rest, so its last assistant message is an answer. */
  terminal: boolean;
  /** Came to rest on a finished turn. */
  atRest: boolean;
  /** Stopped on the human: an approval or a question. */
  needsYou: boolean;
}

export function agentStatusFlags(status: AgentSession["status"] | null): AgentStatusFlags {
  return {
    turnActive: status === "running" || status === "pending" || status === "needs_approval",
    terminal: ["idle", "completed", "interrupted", "failed", "cancelled"].includes(status ?? ""),
    atRest: status === "idle" || status === "completed",
    needsYou: status === "needs_approval" || status === "blocked",
  };
}

const isHumanPrompt = (ev: AgentEvent): boolean =>
  classify(ev) === "user" && !isAutonomyNudge(ev.text);

/**
 * Whether the backend has recorded the real user turn for an optimistic echo:
 * the newest human prompt carries the same text. The goal loop's nudge can land
 * in the same read as the prompt it follows, so it's skipped.
 */
export function pendingLanded(events: AgentEvent[], text: string): boolean {
  for (let i = events.length - 1; i >= 0; i--) {
    if (!isHumanPrompt(events[i])) continue;
    return (events[i].text ?? "").trim() === text.trim();
  }
  return false;
}

/** Prior user turns (initial task + follow-ups) for ↑/↓ history recall. */
export function userPromptHistory(events: AgentEvent[]): string[] {
  return events
    .filter(isHumanPrompt)
    .map((e) => e.text ?? "")
    .filter(Boolean);
}

/** The newest MCP server sign-in the runtime asked for, if any. */
export function latestMcpAuth(events: AgentEvent[]): { server: string; message: string } | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.kind !== "mcp_auth_required") continue;
    const server = ev.tool_name ?? (ev.data?.server as string | undefined) ?? "workiq";
    return { server, message: ev.text ?? "Sign-in required." };
  }
  return null;
}

/**
 * The confirmed history after reading `page`. `reset` means our cursor no longer
 * addresses this transcript (cleared, pruned, or taken against another run) and
 * the payload is a whole replacement. Otherwise append — and keep the same array
 * identity when the delta is empty so an unchanged timeline doesn't re-render.
 */
export function mergeEventPage(archived: AgentEvent[], page: AgentEventPage, fresh: boolean): AgentEvent[] {
  if (page.reset || fresh) return page.events;
  return page.events.length > 0 ? [...archived, ...page.events] : archived;
}

/** `next`, or `prev` when it holds the same values (keeps state identity). */
export function sameOr<T extends readonly number[]>(prev: T, next: T): T {
  return prev.length === next.length && prev.every((v, i) => v === next[i]) ? prev : next;
}

export function sameThinkingOr(
  prev: AgentLiveThinking | null,
  next: AgentLiveThinking | null,
): AgentLiveThinking | null {
  return prev?.text === next?.text && prev?.active === next?.active ? prev : next;
}

/**
 * Only the current SDK session's prompts can be rewound: the current run's, and
 * an earlier run's that continued the same session.
 */
export function withRewindableIds<T extends AgentTurn & { agent_run_id: number | null }>(
  turns: T[],
  rewindableRunIds: readonly number[],
): T[] {
  return turns.map((t) => ({
    ...t,
    event_id:
      t.event_id && t.agent_run_id != null && rewindableRunIds.includes(t.agent_run_id)
        ? t.event_id
        : null,
  }));
}

export type TurnGroup = (index: number) => { key: string | number; label: string };

/** With every run on screen, the rail sets each run's turns apart and names it. */
export function buildTurnGroup(
  turns: AgentTurn[],
  runs: AgentRun[],
  rewindableRunIds: readonly number[],
): TurnGroup | undefined {
  const ids = new Set(turns.map((t) => t.agent_run_id ?? null));
  if (ids.size < 2) return undefined;
  const byId = new Map(runs.map((r) => [r.id, r]));
  return (index) => {
    const id = turns[index]?.agent_run_id ?? null;
    if (id == null) return { key: "none", label: "Before runs were recorded" };
    const run = byId.get(id);
    const label = run ? describeRun(run) : `Run #${id}`;
    return { key: id, label: rewindableRunIds.includes(id) ? `${label} · current session` : label };
  };
}

/**
 * How the follow-up composer handles a message: `/clear` asks first, a bare
 * `/role` opens the role picker, other slash commands run server-side, and
 * anything else starts a turn.
 */
export function followUpKind(message: string): "clear" | "role" | "command" | "prompt" {
  if (/^\/clear\b/i.test(message)) return "clear";
  if (/^\/role\s*$/i.test(message)) return "role";
  return message.startsWith("/") ? "command" : "prompt";
}

export interface PaneInputs {
  /** The user's pick; null follows the agent. */
  choice: AgentPane | null;
  latestVersion: ResultVersion | null;
  anchors: ExchangeAnchor[];
  atRest: boolean;
  showPending: boolean;
  /** A message permalink is open: it lives in the activity. */
  linked: boolean;
}

/**
 * Result or Activity. Left alone the pane follows the agent: the result once
 * it has come to rest on a turn that published one, the live timeline
 * otherwise — while a turn runs, needs you, or stopped short.
 */
export function resolvePane({ choice, latestVersion, anchors, atRest, showPending, linked }: PaneInputs): AgentPane {
  if (linked || !latestVersion) return "activity";
  if (choice) return choice;
  const lastAnchor = anchors.length > 0 ? anchors[anchors.length - 1] : null;
  const fresh = lastAnchor == null || (latestVersion.exchange?.index ?? -1) >= lastAnchor.index;
  return fresh && atRest && !showPending ? "result" : "activity";
}
