import { classify, type StepCategory } from "../components/AgentTimeline";
import { isAutonomyNudge } from "./agentResults";
import type { AgentEvent } from "./types";

/** A grouped tool call: input, output and any pending approval in one box. */
export interface ToolStep {
  key: string;
  toolName: string | null;
  input?: string;
  output?: string;
  done?: boolean;
  pending?: { data: Record<string, unknown>; requestId: string | null };
  /**
   * A permission request was raised for this call. The archived echo carries no
   * request id, so it's correlated positionally — it immediately follows the
   * tool start it gates. In a closed attempt this is what distinguishes "the
   * tool ran and we lost the result" from "it never ran: nobody answered".
   */
  gated?: boolean;
}

export type NodeCategory = "user" | "system" | "assistant" | "reasoning" | "error";

/**
 * A row in the rendered workflow: a centered message node, a grouped tool box,
 * or a side hook bubble.
 */
export type WorkflowRow =
  | {
      type: "node";
      ev: AgentEvent;
      cat: NodeCategory;
      /** An assistant message's thinking, which the SDK sends right after it. */
      reasoning?: string;
    }
  | { type: "tool"; step: ToolStep }
  | { type: "hook"; ev: AgentEvent };

export type NodeRow = Extract<WorkflowRow, { type: "node" }>;

function pick(data: Record<string, unknown> | null, ...keys: string[]): string | undefined {
  if (!data) return undefined;
  for (const k of keys) {
    const v = data[k];
    if (typeof v === "string" && v.trim()) return v;
  }
  return undefined;
}

const isBlank = (text: string | null | undefined): boolean => !text || !text.trim();

// A tool is "done" when its completion event arrives — independent of whether
// it carried any output text (many tools complete silently). Partial-result
// events stream interim output but don't finish the tool.
function isTerminalToolKind(kind: string): boolean {
  return kind.includes("complete") || (kind.includes("result") && !kind.includes("partial"));
}

interface RowBuilder {
  rows: WorkflowRow[];
  toolIndex: Map<string, ToolStep>;
  // A tool box is placed in the flow at its *start*, not at whatever event for
  // it arrives first. Interrupted/resumed turns can stream a tool's completion
  // before its start (out of order); without this, the box floats above the
  // assistant message that requested it. Steps seen completion-first are held
  // in `unplaced` until their start/partial arrives, then appended at the end
  // as a fallback if a start never comes.
  placed: Set<ToolStep>;
  unplaced: ToolStep[];
  // The last tool call that hasn't terminated — what a bare permission echo
  // refers to. The archived `PermissionRequestedData` carries no request id, so
  // this positional link is the only way to know a call stopped at a gate.
  openTool: ToolStep | null;
  // The message a following reasoning event belongs to. The SDK sends a round's
  // complete thinking right after that round's message, before its tool calls.
  thinkingAnchor: NodeRow | null;
}

function placeStep(b: RowBuilder, step: ToolStep): void {
  b.rows.push({ type: "tool", step });
  b.placed.add(step);
}

function toolStepFor(b: RowBuilder, ev: AgentEvent, terminal: boolean): ToolStep {
  const groupKey = ev.request_id ?? `tool-${b.rows.length}`;
  const known = ev.request_id ? b.toolIndex.get(groupKey) : undefined;
  if (known) {
    // The (late) start/partial for a deferred step — place it in order now.
    if (!b.placed.has(known) && !terminal) placeStep(b, known);
    return known;
  }
  const step: ToolStep = { key: groupKey, toolName: ev.tool_name };
  if (ev.request_id) b.toolIndex.set(groupKey, step);
  // Completion before start → defer placement until the start arrives.
  if (terminal) b.unplaced.push(step);
  else placeStep(b, step);
  return step;
}

function addToolEvent(b: RowBuilder, ev: AgentEvent, kind: string): void {
  b.thinkingAnchor = null;
  const terminal = isTerminalToolKind(kind);
  const step = toolStepFor(b, ev, terminal);
  if (ev.tool_name) step.toolName = ev.tool_name;
  const input = pick(ev.data, "arguments", "input");
  if (input) step.input = input;
  if (terminal) {
    const output = pick(ev.data, "result", "output") ?? ev.text ?? undefined;
    if (output) step.output = output;
    step.done = true;
  }
  if (kind === "permission_request") {
    step.pending = { data: (ev.data ?? {}) as Record<string, unknown>, requestId: ev.request_id };
  }
  // Track what a following bare permission echo would refer to.
  b.openTool = step.done ? null : step;
}

function addReasoning(b: RowBuilder, ev: AgentEvent): void {
  // A model with encrypted reasoning reports it with no text at all.
  if (isBlank(ev.text)) return;
  // Shown on the message it belongs to, collapsed above it — think, then
  // speak — like a chat reply's thinking.
  if (b.thinkingAnchor && !b.thinkingAnchor.reasoning) {
    b.thinkingAnchor.reasoning = ev.text ?? "";
  } else {
    b.rows.push({ type: "node", ev, cat: "reasoning" });
  }
  b.thinkingAnchor = null;
}

function addMessageEvent(
  b: RowBuilder,
  ev: AgentEvent,
  cat: Exclude<StepCategory, "skip" | "tool" | "permission">,
): void {
  if (cat === "hook") {
    b.rows.push({ type: "hook", ev });
  } else if (cat === "assistant" && isBlank(ev.text)) {
    // Archives from before streaming frames stopped being kept hold message
    // markers and tool-call-only messages with no text: nothing to render.
    // Its round's thinking has no message to sit on, so it stands alone.
    b.thinkingAnchor = null;
  } else if (cat === "reasoning") {
    addReasoning(b, ev);
  } else {
    const row: NodeRow = { type: "node", ev, cat };
    b.rows.push(row);
    b.thinkingAnchor = cat === "assistant" ? row : null;
  }
}

/**
 * Collapse the raw event stream into renderable rows. Tool start/output and any
 * pending approval that share a request_id are merged into one ToolStep box so
 * the workflow reads as discrete actions rather than scattered events.
 */
export function buildRows(events: AgentEvent[]): WorkflowRow[] {
  const b: RowBuilder = {
    rows: [],
    toolIndex: new Map(),
    placed: new Set(),
    unplaced: [],
    openTool: null,
    thinkingAnchor: null,
  };
  for (const ev of events) {
    const kind = ev.kind.toLowerCase();
    const cat = classify(ev);
    if (cat === "skip") continue;
    // A new round's thinking never belongs to the previous round's message.
    if (kind === "turn_start") b.thinkingAnchor = null;
    // Permission echoes carry no actionable content of their own, but a
    // *request* marks the call before it as gated — the difference between a
    // tool that ran and one that only ever asked.
    if (kind.includes("permissionrequested")) {
      if (b.openTool) b.openTool.gated = true;
      continue;
    }
    if (kind.includes("permissioncompleted")) continue;
    if (cat === "tool" || cat === "permission") addToolEvent(b, ev, kind);
    else addMessageEvent(b, ev, cat);
  }
  // Fallback: tools whose start never arrived (truly broken turn) still render,
  // appended in creation order rather than vanishing.
  for (const step of b.unplaced) {
    if (!b.placed.has(step)) placeStep(b, step);
  }
  return b.rows;
}

function parseAt(at: string | null): number | null {
  if (!at) return null;
  const t = new Date(at).getTime();
  return Number.isNaN(t) ? null : t;
}

/**
 * Per-assistant-answer "generation time" (ms) from event timestamps, mirroring
 * the elapsed readout on topic/chat turns. A turn is anchored at its
 * `turn_start` (or the user prompt, when the SDK omits the marker); each
 * assistant message in that turn is measured from that anchor. Keyed by the
 * event object so the timeline can look it up while rendering.
 */
export function computeElapsedByEvent(events: AgentEvent[]): Map<AgentEvent, number> {
  const map = new Map<AgentEvent, number>();
  let anchorAt: number | null = null;
  for (const ev of events) {
    const atMs = parseAt(ev.at);
    if (ev.kind.toLowerCase() === "turn_start" || classify(ev) === "user") {
      if (atMs != null) anchorAt = atMs;
      continue;
    }
    if (classify(ev) !== "assistant" || isBlank(ev.text) || atMs == null || anchorAt == null) {
      continue;
    }
    const delta = atMs - anchorAt;
    if (delta >= 0) map.set(ev, delta);
  }
  return map;
}

/**
 * The concrete model for each assistant answer, from its turn's usage event.
 * The SDK emits an `AssistantUsageData` (kind "usage") carrying the resolved
 * model just before each assistant message in the same turn, so we track the
 * latest usage model seen since the turn started and tag each answer with it.
 * The call site falls back to the session model when a turn predates this
 * capture (older archived runs) or reported no usage.
 */
export function computeModelByEvent(events: AgentEvent[]): Map<AgentEvent, string> {
  const map = new Map<AgentEvent, string>();
  let currentModel: string | null = null;
  for (const ev of events) {
    const kind = ev.kind.toLowerCase();
    if (kind === "turn_start") {
      currentModel = null;
      continue;
    }
    if (kind === "usage") {
      const m = ev.data?.model;
      if (typeof m === "string" && m.trim()) currentModel = m;
      continue;
    }
    if (classify(ev) === "assistant" && !isBlank(ev.text) && currentModel) {
      map.set(ev, currentModel);
    }
  }
  return map;
}

/**
 * Lifecycle hooks accumulate and attach to the connector *before* the next
 * real row, so they read as side information between steps rather than as
 * steps themselves.
 */
export function segmentRows(rows: WorkflowRow[]): {
  segments: { row: WorkflowRow; hooks: AgentEvent[] }[];
  trailingHooks: AgentEvent[];
} {
  const segments: { row: WorkflowRow; hooks: AgentEvent[] }[] = [];
  let pendingHooks: AgentEvent[] = [];
  for (const row of rows) {
    if (row.type === "hook") {
      pendingHooks.push(row.ev);
      continue;
    }
    segments.push({ row, hooks: pendingHooks });
    pendingHooks = [];
  }
  return { segments, trailingHooks: pendingHooks };
}

/** The goal loop's "keep going" turn, which arrives as a user message. */
export function isNudgeRow(row: WorkflowRow): boolean {
  return row.type === "node" && row.cat === "user" && isAutonomyNudge(row.ev.text);
}

/**
 * Chat-style placement over the workflow spine: the user's own prompts sit to
 * the right, the assistant's answers to the left, and everything else
 * (system, tools, thinking, errors) centered. The cockpit centres the goal
 * loop's nudge as a marker; the read-only trace keeps it on the user side.
 */
export function rowAlign(row: WorkflowRow, { centerNudge }: { centerNudge: boolean }): string {
  if (row.type !== "node") return "justify-center";
  if (row.cat === "user") return centerNudge && isNudgeRow(row) ? "justify-center" : "justify-end";
  return row.cat === "assistant" ? "justify-start" : "justify-center";
}
