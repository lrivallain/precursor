import type { WorkStats } from "../components/AgentTimeline";
import { isAutonomyNudge, type ExchangeAnchor } from "./agentResults";
import type { ShowPrefs } from "./agentShowPrefs";
import { buildRows, type NodeRow, type WorkflowRow } from "./agentWorkflow";
import { parseAgentDirectives, stripAgentDirectives } from "./directives";
import { stripSuggestionBlock } from "./suggestions";
import type { AgentTurn } from "./useAgentRewind";
import type { AgentEvent } from "./types";

export type EntryRole = "prompt" | "answer" | "work";

/** An entry of the cockpit timeline: a row, or the fold standing in for a finished turn's steps. */
export type TimelineEntry =
  | { kind: "row"; key: string; row: WorkflowRow; exchange: number; role: EntryRole }
  | { kind: "fold"; key: string; exchange: number; stats: WorkStats; open: boolean };

export type RowEntry = Extract<TimelineEntry, { kind: "row" }>;

export interface TimelineSegment {
  entry: TimelineEntry;
  // Lifecycle hooks since the previous entry, drawn beside its connector.
  hooks: AgentEvent[];
}

export interface CockpitTimeline {
  segments: TimelineSegment[];
  trailingHooks: AgentEvent[];
  /** Every human prompt, for attributing published results to it. */
  anchors: ExchangeAnchor[];
  /** Segment index of each exchange's prompt — where "Show in activity" lands. */
  exchangeStart: Map<number, number>;
  /** Key of the newest answer, the only one that offers suggested replies. */
  latestAnswerKey: string | null;
  /**
   * One timeline-rail turn per human prompt. `event_id` is the prompt's raw SDK
   * id here; the cockpit clears it on turns outside the current SDK session.
   */
  turns: (AgentTurn & { agent_run_id: number | null })[];
}

export interface CockpitTimelineOptions {
  showPrefs: ShowPrefs;
  /** The agent is at rest, so its last assistant message is an answer. */
  terminal: boolean;
  /** A turn is in flight — its exchange is the live one. */
  live: boolean;
  autonomy: boolean;
  expanded: ReadonlySet<number>;
  targetId?: string | null;
}

interface ExchangeItem {
  row: WorkflowRow;
  idx: number;
}

interface Exchange {
  index: number;
  prompt: NodeRow | null;
  promptIdx: number;
  items: ExchangeItem[];
  answer: WorkflowRow | null;
}

export const excerpt = (text: string | null | undefined, max = 200): string => {
  const flat = (text ?? "").replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
};

const isNode = (row: WorkflowRow, cat: NodeRow["cat"]): row is NodeRow =>
  row.type === "node" && row.cat === cat;

function countRow(stats: WorkStats, row: WorkflowRow): void {
  if (row.type === "tool") stats.tools += 1;
  if (row.type !== "node") return;
  if (row.cat === "reasoning") stats.thoughts += 1;
  if (row.cat === "error") stats.errors += 1;
  if (row.cat === "assistant") {
    stats.messages += 1;
    if (row.reasoning) stats.thoughts += 1;
  }
}

export function workStats(prompt: NodeRow, answer: WorkflowRow, work: { row: WorkflowRow }[]): WorkStats {
  const stats: WorkStats = { durationMs: null, tools: 0, thoughts: 0, messages: 0, errors: 0 };
  for (const { row } of work) countRow(stats, row);
  const start = Date.parse(prompt.ev.at ?? "");
  const end = answer.type === "node" ? Date.parse(answer.ev.at ?? "") : Number.NaN;
  if (!Number.isNaN(start) && !Number.isNaN(end) && end >= start) stats.durationMs = end - start;
  return stats;
}

// A turn's answer is its last assistant message before the session idled (or
// before now, once the agent has come to rest).
function findAnswerRows(rows: WorkflowRow[], terminal: boolean): Set<WorkflowRow> {
  const answers = new Set<WorkflowRow>();
  let lastAssistant: WorkflowRow | null = null;
  for (const r of rows) {
    if (isNode(r, "assistant")) {
      lastAssistant = r;
    } else if (r.type === "hook" && r.ev.kind.toLowerCase().includes("idle")) {
      if (lastAssistant) answers.add(lastAssistant);
      lastAssistant = null;
    }
  }
  if (terminal && lastAssistant) answers.add(lastAssistant);
  return answers;
}

function groupExchanges(rows: WorkflowRow[]): Exchange[] {
  const exchanges: Exchange[] = [];
  rows.forEach((row, idx) => {
    // The goal loop's nudge arrives as a user turn but continues the exchange.
    const opens = isNode(row, "user") && !isAutonomyNudge(row.ev.text);
    if (opens || exchanges.length === 0) {
      exchanges.push({
        index: exchanges.length,
        prompt: opens ? row : null,
        promptIdx: idx,
        items: [],
        answer: null,
      });
      if (opens) return;
    }
    exchanges[exchanges.length - 1].items.push({ row, idx });
  });
  return exchanges;
}

function assignAnswers(exchanges: Exchange[], answerRows: Set<WorkflowRow>): void {
  exchanges.forEach((ex, i) => {
    for (const { row } of ex.items) if (answerRows.has(row)) ex.answer = row;
    // A turn a later prompt followed has ended even without an idle marker
    // (older archives): its last assistant message is what it concluded with.
    if (ex.answer || i === exchanges.length - 1) return;
    for (const { row } of ex.items) if (isNode(row, "assistant")) ex.answer = row;
  });
}

const linksTo = (row: WorkflowRow, targetId: string | null | undefined): boolean =>
  Boolean(targetId) && row.type === "node" && row.ev.link_id === targetId;

function rowVisible(row: WorkflowRow, isAnswer: boolean, opts: CockpitTimelineOptions): boolean {
  const prefs = opts.showPrefs;
  if (linksTo(row, opts.targetId)) return true;
  if (row.type === "hook") return prefs.lifecycle;
  if (row.type === "tool") return prefs.tool;
  if (row.cat === "user" && isAutonomyNudge(row.ev.text)) return prefs.lifecycle;
  if (row.cat === "system") return prefs.system;
  if (row.cat === "reasoning") return prefs.thinking;
  // Keep answers visible even when assistant chatter is hidden.
  if (row.cat === "assistant") return prefs.assistant || isAnswer;
  return true;
}

function answerExcerpt(answer: WorkflowRow | null): string {
  if (answer?.type !== "node") return "";
  return excerpt(stripAgentDirectives(stripSuggestionBlock(answer.ev.text ?? "")));
}

function buildTurns(exchanges: Exchange[]): CockpitTimeline["turns"] {
  const turns: CockpitTimeline["turns"] = [];
  for (const ex of exchanges) {
    if (!ex.prompt) continue;
    const ev = ex.prompt.ev;
    turns.push({
      message_id: ex.index,
      last_message_id: ex.index,
      created_at: ev.at ?? "",
      prompt: excerpt(ev.text),
      reply: answerExcerpt(ex.answer),
      has_compaction: ex.items.some(
        (it) => it.row.type === "hook" && it.row.ev.kind === "compaction",
      ),
      agent_session_id: null,
      event_id: ev.event_id ?? null,
      text: ev.text ?? "",
      agent_run_id: ev.agent_run_id,
    });
  }
  return turns;
}

function exchangeAnchor(ex: Exchange, prompt: NodeRow, autonomy: boolean): ExchangeAnchor {
  return {
    index: ex.index,
    at: prompt.ev.at,
    prompt: prompt.ev.text ?? null,
    summary:
      autonomy && ex.answer?.type === "node"
        ? parseAgentDirectives(ex.answer.ev.text).complete
        : null,
  };
}

// The work of a turn stays inline when folding is off, the turn has no answer
// yet, holds an approval card, or is the one in flight.
function keepsOpen(ex: Exchange, work: ExchangeItem[], foldable: boolean): boolean {
  if (!foldable || work.length === 0) return true;
  return ex.items.some((it) => it.row.type === "tool" && Boolean(it.row.step.pending));
}

/** Timeline entries of one exchange, and the key of its answer, if any. */
function exchangeEntries(ex: Exchange, isLive: boolean, opts: CockpitTimelineOptions): {
  entries: TimelineEntry[];
  answerKey: string | null;
} {
  const entry = (it: ExchangeItem, role: EntryRole): TimelineEntry => ({
    kind: "row",
    key: `r${it.idx}`,
    row: it.row,
    exchange: ex.index,
    role,
  });
  const entries: TimelineEntry[] = [];
  if (ex.prompt) entries.push(entry({ row: ex.prompt, idx: ex.promptIdx }, "prompt"));
  const answerItem = ex.items.find((it) => it.row === ex.answer);
  const answerKey = answerItem ? `r${answerItem.idx}` : null;
  const work = ex.items.filter((it) => it !== answerItem);
  const pushWork = (items: ExchangeItem[]): void => {
    for (const it of items) {
      const isAnswer = it === answerItem;
      if (rowVisible(it.row, isAnswer, opts)) entries.push(entry(it, isAnswer ? "answer" : "work"));
    }
  };
  if (!ex.prompt || !answerItem || isLive || keepsOpen(ex, work, opts.showPrefs.fold)) {
    pushWork(ex.items);
    return { entries, answerKey };
  }
  const open =
    opts.expanded.has(ex.index) || work.some((it) => linksTo(it.row, opts.targetId));
  entries.push({
    kind: "fold",
    key: `f${ex.index}`,
    exchange: ex.index,
    stats: workStats(ex.prompt, answerItem.row, work),
    open,
  });
  if (open) pushWork(work);
  entries.push(entry(answerItem, "answer"));
  return { entries, answerKey };
}

function segmentEntries(entries: TimelineEntry[]): Pick<CockpitTimeline, "segments" | "trailingHooks" | "exchangeStart"> {
  const segments: TimelineSegment[] = [];
  const exchangeStart = new Map<number, number>();
  let pendingHooks: AgentEvent[] = [];
  for (const e of entries) {
    if (e.kind === "row" && e.row.type === "hook") {
      pendingHooks.push(e.row.ev);
      continue;
    }
    if (e.kind === "row" && e.role === "prompt") exchangeStart.set(e.exchange, segments.length);
    segments.push({ entry: e, hooks: pendingHooks });
    pendingHooks = [];
  }
  return { segments, trailingHooks: pendingHooks, exchangeStart };
}

/**
 * Shape the cockpit timeline as exchanges: each human prompt, the work it set
 * off, and the answer that concluded it.
 *
 * Once a turn has its answer, the work in between folds into a one-line summary
 * (unless the "Fold finished turns" preference is off or the user opened it),
 * so a session refined over several follow-ups reads as prompt → answer →
 * prompt → answer instead of hundreds of tool boxes. The turn still in flight,
 * and any turn holding an approval card, stay open.
 */
export function buildCockpitTimeline(events: AgentEvent[], opts: CockpitTimelineOptions): CockpitTimeline {
  const rows = buildRows(events);
  const exchanges = groupExchanges(rows);
  assignAnswers(exchanges, findAnswerRows(rows, opts.terminal));

  const entries: TimelineEntry[] = [];
  const anchors: ExchangeAnchor[] = [];
  let latestAnswerKey: string | null = null;
  exchanges.forEach((ex, i) => {
    if (ex.prompt) anchors.push(exchangeAnchor(ex, ex.prompt, opts.autonomy));
    const isLive = opts.live && i === exchanges.length - 1;
    const built = exchangeEntries(ex, isLive, opts);
    entries.push(...built.entries);
    if (built.answerKey) latestAnswerKey = built.answerKey;
  });
  return { ...segmentEntries(entries), anchors, latestAnswerKey, turns: buildTurns(exchanges) };
}
