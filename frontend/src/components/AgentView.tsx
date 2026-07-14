import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  Bot,
  Brain,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleDot,
  Code2,
  Cog,
  Copy,
  Eye,
  FileText,
  Globe,
  Loader2,
  PlayCircle,
  Settings as SettingsIcon,
  ShieldQuestion,
  Sparkles,
  Terminal,
  User,
  Wrench,
  X,
} from "lucide-react";
import { api } from "../lib/api";
import { eventBus } from "../lib/events";
import { mcpAuthStore } from "../lib/mcpAuth";
import { matchAgentSlashCommands, type SlashCommand } from "../lib/commands";
import { useSettings } from "../lib/settingsStore";
import { parseSuggestions, stripSuggestionBlock } from "../lib/suggestions";
import { useAzureSpeech } from "../lib/useAzureSpeech";
import { useResizableHeight } from "../lib/useResizableHeight";
import { Composer } from "./Composer";
import { ComposerModelControls } from "./ComposerModelControls";
import { Markdown } from "./Markdown";
import { MessageMeta } from "./MessageMeta";
import { SuggestedReplies } from "./SuggestedReplies";
import { TopicPicker } from "./TopicPicker";
import type {
  AgentEvent,
  AgentPermissionDecisionValue,
  AgentSession,
  Me,
  Topic,
} from "../lib/types";

interface AgentViewProps {
  agents: AgentSession[];
  agentId: number | null;
  enabled: boolean;
  available: boolean;
  unavailableReason: string | null;
  /** Re-fetch the session list in the parent (status changed, created, deleted). */
  onReload: () => void;
  /** Select a session (or clear with null to show the start form). */
  onSelect: (id: number | null) => void;
  onOpenSettings: () => void;
  /** Topic to preselect in the new-agent form (e.g. opened from a topic). */
  draftTopicId?: number | null;
}

// One step in the workflow timeline.
type DecisionHandler = (
  requestId: string,
  decision: AgentPermissionDecisionValue,
) => void | Promise<void>;

// Coarse category every event maps to. Drives one consistent color scheme so
// the same kind always looks the same across every agent conversation.
type StepCategory =
  | "user"
  | "system"
  | "assistant"
  | "reasoning"
  | "tool"
  | "permission"
  | "error"
  | "hook"
  | "skip";

// Per-category visuals: a solid pastel fill + matching marker. Theme-aware via
// alpha so both light and dark modes stay legible.
const CATEGORY_STYLE: Record<
  Exclude<StepCategory, "skip" | "hook">,
  { label: string; box: string; marker: string; icon: React.ReactNode }
> = {
  user: {
    label: "User prompt",
    box: "border-sky-500/30 bg-sky-500/15",
    marker: "border-sky-500/40 bg-sky-500/20 text-sky-600 dark:text-sky-300",
    icon: <User size={13} />,
  },
  system: {
    label: "System",
    box: "border-orange-400/30 bg-orange-400/15",
    marker: "border-orange-400/40 bg-orange-400/20 text-orange-600 dark:text-orange-300",
    icon: <Cog size={13} />,
  },
  assistant: {
    label: "Assistant",
    box: "border-border bg-surface",
    marker: "border-border bg-surface text-muted",
    icon: <Sparkles size={13} />,
  },
  reasoning: {
    label: "Thinking",
    box: "border-cyan-500/30 bg-cyan-500/10",
    marker: "border-cyan-500/40 bg-cyan-500/15 text-cyan-600 dark:text-cyan-300",
    icon: <Brain size={13} />,
  },
  tool: {
    label: "Tool",
    box: "border-violet-500/30 bg-violet-500/12",
    marker: "border-violet-500/40 bg-violet-500/20 text-violet-600 dark:text-violet-300",
    icon: <Wrench size={13} />,
  },
  permission: {
    label: "Permission",
    box: "border-orange-500/40 bg-orange-500/10",
    marker: "border-orange-500/50 bg-orange-500/15 text-orange-600 dark:text-orange-400",
    icon: <ShieldQuestion size={13} />,
  },
  error: {
    label: "Error",
    box: "border-red-500/40 bg-red-500/12",
    marker: "border-red-500/50 bg-red-500/15 text-red-500",
    icon: <AlertTriangle size={13} />,
  },
};

// Map a raw event onto a category. Unmapped kinds fall back by substring so new
// SDK event names still land somewhere sensible.
function classify(event: AgentEvent): StepCategory {
  const kind = event.kind.toLowerCase();
  if (kind.includes("delta")) return "skip";
  // Surfaced as the global sign-in banner, not as a timeline node.
  if (kind === "mcp_auth_required") return "skip";
  if (kind === "permission_request") return "permission";
  if (event.tool_name || kind.startsWith("tool")) return "tool";
  if (kind.includes("reason") || kind.includes("think")) return "reasoning";
  if (kind.includes("error")) return "error";
  if (
    kind.includes("turn") ||
    kind.includes("usage") ||
    kind.includes("idle") ||
    kind.includes("session") ||
    kind.includes("abort")
  )
    return "hook";
  if (kind.includes("user")) return "user";
  if (kind.includes("system")) return "system";
  if (kind.includes("assistant") || kind === "message") return "assistant";
  return "hook";
}

// A tool icon keyed off the tool's name (shell/file/url) for quick scanning.
function toolIcon(name: string | null): React.ReactNode {
  const n = (name ?? "").toLowerCase();
  if (n.includes("shell") || n.includes("bash") || n.includes("command"))
    return <Terminal size={13} />;
  if (n.includes("write") || n.includes("file") || n.includes("read") || n.includes("edit"))
    return <FileText size={13} />;
  if (n.includes("url") || n.includes("fetch") || n.includes("search") || n.includes("web"))
    return <Globe size={13} />;
  return <Wrench size={13} />;
}

// A "transition" hook (turn start/end, usage, idle…) rendered as a small yellow
// bubble floated to the right of the workflow — side information, out of the way.
function HookBubble({ event }: { event: AgentEvent }) {
  return (
    <div
      className="my-0.5 flex items-center gap-1 self-end rounded-full border border-yellow-400/40 bg-yellow-400/15 px-2 py-0.5 text-[9px] uppercase tracking-wide text-yellow-700 dark:text-yellow-300"
      title="Lifecycle hook"
    >
      <CircleDot size={8} className="opacity-70" />
      {event.kind.replace(/_/g, " ").replace(/data$/i, "").trim()}
    </div>
  );
}

// The centered link drawn between two consecutive workflow boxes.
function Connector() {
  return (
    <div className="group flex flex-col items-center px-6 py-0.5" aria-hidden>
      <span className="-mt-1 h-2 w-2 rounded-full bg-muted/50 transition-colors group-hover:bg-accent" />
      <span className="h-3 w-0.5 rounded-full bg-muted/50 transition-colors group-hover:bg-accent" />
      <ChevronDown
        size={18}
        strokeWidth={2.5}
        className="-mt-1.5 -mb-1.5 text-muted/70 transition-colors group-hover:text-accent"
      />
    </div>
  );
}

// The link between two main boxes, with any lifecycle hooks that happened in
// between floated to the right of the arrow — so the arrow always sits directly
// between the steps and the hooks never push the boxes apart.
function StepConnector({ hooks }: { hooks: AgentEvent[] }) {
  if (hooks.length === 0) return <Connector />;
  return (
    <div className="group relative flex w-full max-w-xl justify-end py-1">
      <div
        className="absolute -inset-y-1 left-1/2 flex -translate-x-1/2 flex-col items-center"
        aria-hidden
      >
        <span className="h-2 w-2 shrink-0 rounded-full bg-muted/50 transition-colors group-hover:bg-accent" />
        <span className="w-0.5 flex-1 rounded-full bg-muted/50 transition-colors group-hover:bg-accent" />
        <ChevronDown
          size={18}
          strokeWidth={2.5}
          className="-mt-1.5 shrink-0 text-muted/70 transition-colors group-hover:text-accent"
        />
      </div>
      <div className="relative flex flex-col items-end gap-0.5">
        {hooks.map((ev, i) => (
          <HookBubble key={i} event={ev} />
        ))}
      </div>
    </div>
  );
}

// Hooks before the first box or after the last one, with no arrow to attach to.
function HookGutter({ hooks }: { hooks: AgentEvent[] }) {
  return (
    <div className="flex w-full max-w-xl flex-col items-end gap-0.5 py-1">
      {hooks.map((ev, i) => (
        <HookBubble key={i} event={ev} />
      ))}
    </div>
  );
}

// A small on/off filter pill for the workflow display toggles.
function ToggleChip({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] transition ${
        active
          ? "border-accent/40 bg-accent/15 text-accent"
          : "border-border text-muted hover:text-fg"
      }`}
    >
      {active ? <Check size={10} /> : <X size={10} className="opacity-50" />}
      {label}
    </button>
  );
}

// Per-agent token usage distilled from the workflow timeline. `usage` events
// (one per metered LLM round) carry input/output tokens; `context_usage` events
// report the live context-window occupancy. Mirrors the conversation-stats
// aside on topics/chats, but sourced from the agent's own event stream.
interface AgentUsage {
  lastInput: number;
  lastOutput: number;
  totalInput: number;
  totalOutput: number;
  rounds: number;
  contextUsed: number | null;
  contextLimit: number | null;
}

function numField(data: Record<string, unknown> | null, key: string): number {
  const v = data?.[key];
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

function computeAgentUsage(events: AgentEvent[]): AgentUsage {
  let lastInput = 0;
  let lastOutput = 0;
  let totalInput = 0;
  let totalOutput = 0;
  let rounds = 0;
  let contextUsed: number | null = null;
  let contextLimit: number | null = null;
  for (const ev of events) {
    const kind = ev.kind.toLowerCase();
    if (kind === "usage") {
      const input = numField(ev.data, "input_tokens");
      const output = numField(ev.data, "output_tokens");
      // Skip rounds that reported no tokens so they don't reset "last turn".
      if (input === 0 && output === 0) continue;
      totalInput += input;
      totalOutput += output;
      lastInput = input;
      lastOutput = output;
      rounds += 1;
    } else if (kind === "context_usage") {
      const limit = numField(ev.data, "token_limit");
      if (limit > 0) {
        contextUsed = numField(ev.data, "current_tokens");
        contextLimit = limit;
      }
    }
  }
  return { lastInput, lastOutput, totalInput, totalOutput, rounds, contextUsed, contextLimit };
}

function formatTokens(n: number): string {
  return n.toLocaleString();
}

function compactTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return (n / 1000).toFixed(1) + "k";
  if (n < 1_000_000) return Math.round(n / 1000) + "k";
  return (n / 1_000_000).toFixed(1) + "M";
}

function UsageStat({
  label,
  value,
  emphasis,
}: {
  label: string;
  value: string;
  emphasis?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-muted">{label}</span>
      <span className={`tabular-nums ${emphasis ? "font-semibold" : ""}`}>{value}</span>
    </div>
  );
}

function UsageGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">{title}</div>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

// Context-window occupancy bar — green/amber/rose as it fills, like the
// topic/chat conversation stats.
function ContextWindowBar({
  used,
  limit,
  model,
}: {
  used: number;
  limit: number;
  model: string | null;
}) {
  const pct = Math.min(100, Math.max(0, (used / limit) * 100));
  const tone = pct >= 85 ? "bg-rose-500" : pct >= 60 ? "bg-amber-500" : "bg-emerald-500";
  return (
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">Context window</div>
      <div
        className="h-2 w-full overflow-hidden rounded bg-border"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={limit}
        aria-valuenow={used}
        aria-label={`Context window used: ${used} of ${limit} tokens`}
      >
        <div className={`h-full ${tone} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <div className="mt-1 flex justify-between text-[11px] tabular-nums text-muted">
        <span>
          {compactTokens(used)} / {compactTokens(limit)}
        </span>
        <span>{pct.toFixed(pct < 10 ? 1 : 0)}%</span>
      </div>
      {model && (
        <div className="mt-0.5 truncate text-[10px] text-muted" title={model}>
          {model}
        </div>
      )}
    </div>
  );
}

function AgentUsageSection({
  events,
  model,
}: {
  events: AgentEvent[];
  model: string | null;
}) {
  const usage = useMemo(() => computeAgentUsage(events), [events]);
  const hasUsage = usage.rounds > 0 || usage.contextLimit !== null;
  return (
    <div className="space-y-3 text-sm">
      <div className="flex items-center gap-1.5 text-sm font-medium">
        <BarChart3 size={14} />
        <span>Usage</span>
      </div>
      {!hasUsage ? (
        <p className="text-[11px] text-muted">No usage reported yet.</p>
      ) : (
        <>
          {usage.contextLimit !== null && usage.contextUsed !== null && (
            <ContextWindowBar
              used={usage.contextUsed}
              limit={usage.contextLimit}
              model={model}
            />
          )}
          <UsageGroup title="Last turn">
            <UsageStat label="Input" value={formatTokens(usage.lastInput)} />
            <UsageStat label="Output" value={formatTokens(usage.lastOutput)} />
          </UsageGroup>
          <UsageGroup title="Cumulative">
            <UsageStat label="Input" value={formatTokens(usage.totalInput)} />
            <UsageStat label="Output" value={formatTokens(usage.totalOutput)} />
            <UsageStat
              label="Total"
              value={formatTokens(usage.totalInput + usage.totalOutput)}
              emphasis
            />
            <UsageStat label="Rounds" value={String(usage.rounds)} />
          </UsageGroup>
        </>
      )}
    </div>
  );
}

// Collapsible right-hand panel mirroring the conversation-stats aside on
// topics/chats: a token-usage readout plus the workflow "Show" filters.
// Collapse state is persisted.
const SHOW_PANEL_KEY = "precursor:agent-show-panel:collapsed";

function AgentInsightsPanel({
  showPrefs,
  toggleShow,
  events,
  model,
}: {
  showPrefs: ShowPrefs;
  toggleShow: (k: keyof ShowPrefs) => void;
  events: AgentEvent[];
  model: string | null;
}) {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    // Hidden by default — only stay open when the user explicitly expanded it.
    return window.localStorage.getItem(SHOW_PANEL_KEY) !== "0";
  });
  useEffect(() => {
    window.localStorage.setItem(SHOW_PANEL_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  if (collapsed) {
    return (
      <aside className="flex w-9 shrink-0 flex-col items-center border-l border-border bg-surface/40 px-1 py-2">
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          className="rounded p-1.5 text-muted hover:bg-surface"
          data-tooltip="Show usage & filters"
          aria-label="Show usage and workflow filters"
        >
          <ChevronLeft size={16} />
        </button>
        <BarChart3 size={16} className="mt-2 text-muted" />
        <Eye size={16} className="mt-2 text-muted" />
      </aside>
    );
  }

  return (
    <aside className="flex w-60 shrink-0 flex-col border-l border-border bg-surface/30">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          <span>Insights</span>
        </div>
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          className="rounded p-1 text-muted hover:bg-surface"
          data-tooltip="Collapse panel"
          aria-label="Collapse panel"
        >
          <ChevronRight size={16} />
        </button>
      </div>
      <div className="flex flex-col gap-4 overflow-y-auto p-3">
        <AgentUsageSection events={events} model={model} />
        <div className="border-t border-border" />
        <div className="space-y-2">
          <div className="flex items-center gap-1.5 text-sm font-medium">
            <Eye size={14} />
            <span>Show</span>
          </div>
          <div className="flex flex-col items-start gap-1.5">
            <ToggleChip
              active={showPrefs.system}
              onClick={() => toggleShow("system")}
              label="System"
            />
            <ToggleChip
              active={showPrefs.assistant}
              onClick={() => toggleShow("assistant")}
              label="Assistant"
            />
            <ToggleChip
              active={showPrefs.thinking}
              onClick={() => toggleShow("thinking")}
              label="Thinking"
            />
            <ToggleChip active={showPrefs.tool} onClick={() => toggleShow("tool")} label="Tool" />
            <ToggleChip
              active={showPrefs.lifecycle}
              onClick={() => toggleShow("lifecycle")}
              label="Lifecycle"
            />
          </div>
        </div>
      </div>
    </aside>
  );
}

// Renders the details + approve/deny controls for an inline permission request.
function PermissionBody({
  data,
  requestId,
  busy,
  onDecision,
}: {
  data: Record<string, unknown>;
  requestId: string | null;
  busy: boolean;
  onDecision: DecisionHandler;
}) {
  const str = (k: string): string | null => {
    const v = data[k];
    return typeof v === "string" && v.trim() ? v : null;
  };
  const command = str("command");
  const path = str("path");
  const url = str("url");
  const server = str("server");
  const tool = str("tool");
  const intention = str("intention");
  const warning = str("warning");
  const diff = str("diff");
  const fact = str("fact");
  const reason = str("reason");
  const detail = str("detail");

  return (
    <div className="mt-1 space-y-1.5">
      {intention && <p className="text-[11px] text-muted">{intention}</p>}
      {command && (
        <pre className="overflow-x-auto rounded bg-bg px-2 py-1 font-mono text-[11px]">
          {command}
        </pre>
      )}
      {path && (
        <p className="font-mono text-[11px]">
          <span className="text-muted">path: </span>
          {path}
        </p>
      )}
      {url && (
        <p className="break-all font-mono text-[11px]">
          <span className="text-muted">url: </span>
          {url}
        </p>
      )}
      {(server || tool) && (
        <p className="font-mono text-[11px]">
          <span className="text-muted">tool: </span>
          {[server, tool].filter(Boolean).join(" · ")}
        </p>
      )}
      {fact && (
        <p className="text-[11px]">
          <span className="text-muted">remember: </span>
          {fact}
        </p>
      )}
      {reason && <p className="text-[11px] text-muted">{reason}</p>}
      {detail && <p className="text-[11px] text-muted">{detail}</p>}
      {diff && (
        <pre className="max-h-48 overflow-auto rounded bg-bg px-2 py-1 font-mono text-[10px] leading-snug">
          {diff}
        </pre>
      )}
      {warning && (
        <p className="rounded bg-amber-500/10 px-2 py-1 text-[11px] text-amber-600 dark:text-amber-400">
          ⚠ {warning}
        </p>
      )}
      {requestId && (
        <div className="flex flex-wrap gap-1.5 pt-0.5">
          <button
            type="button"
            onClick={() => void onDecision(requestId, "approve-once")}
            disabled={busy}
            className="rounded bg-accent px-2 py-1 text-[11px] text-white disabled:opacity-50"
          >
            Approve once
          </button>
          <button
            type="button"
            onClick={() => void onDecision(requestId, "approve-always")}
            disabled={busy}
            className="rounded border border-border px-2 py-1 text-[11px] disabled:opacity-50"
          >
            Approve for session
          </button>
          <button
            type="button"
            onClick={() => void onDecision(requestId, "deny")}
            disabled={busy}
            className="flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] text-red-500 disabled:opacity-50"
          >
            <X size={12} /> Deny
          </button>
        </div>
      )}
    </div>
  );
}

// A grouped tool call: input, output and any pending approval in one box.
interface ToolStep {
  key: string;
  toolName: string | null;
  input?: string;
  output?: string;
  done?: boolean;
  pending?: { data: Record<string, unknown>; requestId: string | null };
}

// One simple message node (user/system/assistant/reasoning/error).
function MessageNode({
  event,
  category,
  isLastAnswer,
  user,
  model,
  elapsedMs,
  onPickSuggestion,
  suggestionsDisabled,
}: {
  event: AgentEvent;
  category: "user" | "system" | "assistant" | "reasoning" | "error";
  isLastAnswer: boolean;
  user?: { name: string; avatarUrl: string | null };
  // Session model + computed generation time, shown on assistant answers only.
  model?: string | null;
  elapsedMs?: number | null;
  onPickSuggestion?: (text: string) => void;
  suggestionsDisabled?: boolean;
}) {
  const style = CATEGORY_STYLE[category];
  const box = isLastAnswer
    ? "border-emerald-500/50 bg-emerald-500/15 ring-1 ring-emerald-500/30"
    : style.box;
  const marker = isLastAnswer
    ? "border-emerald-500/50 bg-emerald-500/20 text-emerald-600 dark:text-emerald-300"
    : style.marker;
  // For the user's own prompt, show their GitHub persona (avatar + name) rather
  // than a generic icon/label.
  const isUser = category === "user";
  const isSystem = category === "system";
  const isAssistant = category === "assistant";
  const label = isUser && user ? user.name : style.label;
  // Assistant turns may end with a `suggest` block; hide it from the rendered
  // text and surface it as chips under the final answer instead.
  const assistantText = isAssistant ? stripSuggestionBlock(event.text ?? "") : (event.text ?? "");
  const suggestions =
    isAssistant && isLastAnswer && onPickSuggestion
      ? parseSuggestions(event.text ?? "")
      : [];
  // The system message (the long base prompt) is collapsed to a few lines with
  // a Details toggle, like a tool box, so it never floods the timeline.
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState<null | "text" | "md">(null);
  const contentRef = useRef<HTMLDivElement>(null);
  // Copy the rendered text (markdown stripped) or the raw markdown source, the
  // same pair of actions offered on topic/chat assistant bubbles.
  const copyTo = async (kind: "text" | "md") => {
    const value =
      kind === "md"
        ? (isAssistant ? assistantText : (event.text ?? ""))
        : (contentRef.current?.textContent ?? event.text ?? "").trim();
    try {
      await navigator.clipboard.writeText(value);
      setCopied(kind);
      window.setTimeout(() => setCopied(null), 1200);
    } catch {
      // Clipboard may be unavailable (e.g. insecure context); fail silently.
    }
  };
  return (
    <div
      className={`group/node relative w-full max-w-xl rounded-lg border p-2.5 transition hover:border-accent hover:ring-2 hover:ring-accent/40 ${box}`}
    >
      <div className="flex items-center gap-2">
        {isUser && user?.avatarUrl ? (
          <img
            src={user.avatarUrl}
            alt={user.name}
            className="h-6 w-6 shrink-0 rounded-full border border-sky-500/40 object-cover"
          />
        ) : (
          <span
            className={`grid h-6 w-6 shrink-0 place-items-center rounded-full border ${marker}`}
          >
            {style.icon}
          </span>
        )}
        <span className={`text-[11px] font-semibold ${isUser && user ? "" : "capitalize"}`}>
          {label}
        </span>
        {isLastAnswer && (
          <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-emerald-600 dark:text-emerald-400">
            Answer
          </span>
        )}
      </div>
      {event.text &&
        (isSystem ? (
          <div className="mt-1">
            <p
              className={`whitespace-pre-wrap text-[11px] text-muted ${open ? "" : "line-clamp-3"}`}
            >
              {event.text}
            </p>
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="mt-1 flex items-center gap-0.5 rounded px-1 py-0.5 text-[10px] text-muted hover:bg-bg"
              title={open ? "Collapse" : "Show full system message"}
            >
              <ChevronDown
                size={12}
                className={`transition-transform ${open ? "rotate-180" : ""}`}
              />
              {open ? "Hide" : "Details"}
            </button>
          </div>
        ) : isAssistant ? (
          <div ref={contentRef}>
            <Markdown className="mt-1 text-[11px] leading-relaxed text-muted">
              {assistantText}
            </Markdown>
          </div>
        ) : (
          <p className="mt-1 whitespace-pre-wrap text-[11px] text-muted">
            {event.text.length > 1500 ? `${event.text.slice(0, 1500)}…` : event.text}
          </p>
        ))}
      {isAssistant && event.text && (
        <div className="absolute -bottom-3 right-2 z-10 flex items-center gap-1 rounded-full border border-border bg-surface px-1 py-0.5 opacity-0 shadow-sm transition-opacity group-hover/node:opacity-100">
          <button
            type="button"
            onClick={() => copyTo("text")}
            className="rounded-full p-1 text-muted hover:text-accent"
            aria-label="Copy message"
            data-tooltip="Copy message"
          >
            {copied === "text" ? (
              <Check size={12} className="text-emerald-500" />
            ) : (
              <Copy size={12} />
            )}
          </button>
          <button
            type="button"
            onClick={() => copyTo("md")}
            className="rounded-full p-1 text-muted hover:text-accent"
            aria-label="Copy raw markdown"
            data-tooltip="Copy raw markdown"
          >
            {copied === "md" ? (
              <Check size={12} className="text-emerald-500" />
            ) : (
              <Code2 size={12} />
            )}
          </button>
        </div>
      )}
      {suggestions.length > 0 && onPickSuggestion && (
        <SuggestedReplies
          items={suggestions}
          onPick={onPickSuggestion}
          disabled={suggestionsDisabled}
          className="mt-2"
        />
      )}
      <div className="mt-1.5">
        <MessageMeta
          createdAt={event.at}
          model={isAssistant ? model : null}
          elapsedMs={isAssistant ? elapsedMs : null}
          hoverGroup="node"
        />
      </div>
    </div>
  );
}

// Optimistic echo of a just-sent prompt. Rendered immediately on send — before
// the backend has recorded the real user turn — so it's obvious the message
// left the composer even while the session spins the turn up. Styled like the
// real user node but muted, with a "Sending…" spinner; it's swapped out for the
// real event the moment it lands.
function PendingUserBubble({
  text,
  user,
}: {
  text: string;
  user?: { name: string; avatarUrl: string | null };
}) {
  const style = CATEGORY_STYLE.user;
  return (
    <div
      className={`relative w-full max-w-xl rounded-lg border p-2.5 opacity-70 ${style.box}`}
      aria-busy="true"
    >
      <div className="flex items-center gap-2">
        {user?.avatarUrl ? (
          <img
            src={user.avatarUrl}
            alt={user.name}
            className="h-6 w-6 shrink-0 rounded-full border border-sky-500/40 object-cover"
          />
        ) : (
          <span
            className={`grid h-6 w-6 shrink-0 place-items-center rounded-full border ${style.marker}`}
          >
            {style.icon}
          </span>
        )}
        <span className="text-[11px] font-semibold">{user?.name ?? "You"}</span>
        <span className="ml-auto flex items-center gap-1 text-[10px] text-muted">
          <Loader2 size={11} className="animate-spin" />
          Sending…
        </span>
      </div>
      <p className="mt-1 whitespace-pre-wrap text-[11px] text-muted">
        {text.length > 1500 ? `${text.slice(0, 1500)}…` : text}
      </p>
    </div>
  );
}

// A grouped tool call. Shows the tool name + status; input/output collapse until
// the user wants to see "what was done"; a pending approval renders inline.
function ToolBox({
  step,
  busy,
  onDecision,
}: {
  step: ToolStep;
  busy: boolean;
  onDecision: DecisionHandler;
}) {
  const [open, setOpen] = useState(false);
  const detailOpen = open;
  const hasDetail = Boolean(step.input || step.output);
  const status = step.pending
    ? "awaiting approval"
    : step.done
      ? "done"
      : "running";
  const style = CATEGORY_STYLE.tool;
  const box = step.pending ? CATEGORY_STYLE.permission.box : style.box;
  const marker = step.pending ? CATEGORY_STYLE.permission.marker : style.marker;

  return (
    <div
      className={`w-full max-w-xl rounded-lg border p-2.5 transition hover:border-accent hover:ring-2 hover:ring-accent/40 ${box}`}
    >
      <div className="flex items-center gap-2">
        <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full border ${marker}`}>
          {status === "running" ? (
            <Loader2 size={13} className="animate-spin" />
          ) : step.pending ? (
            <ShieldQuestion size={13} />
          ) : (
            toolIcon(step.toolName)
          )}
        </span>
        <span className="truncate text-[11px] font-semibold">{step.toolName || "Tool"}</span>
        <span className="text-[10px] text-muted">{status}</span>
        {hasDetail && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="ml-auto flex items-center gap-0.5 rounded px-1 py-0.5 text-[10px] text-muted hover:bg-bg"
            title={detailOpen ? "Hide details" : "Show what was done"}
          >
            <ChevronDown
              size={12}
              className={`transition-transform ${detailOpen ? "rotate-180" : ""}`}
            />
            {detailOpen ? "Hide" : "Details"}
          </button>
        )}
      </div>

      {detailOpen && hasDetail && (
        <div className="mt-1.5 space-y-1.5">
          {step.input && <ToolField label="input" value={step.input} />}
          {step.output && <ToolField label="output" value={step.output} />}
        </div>
      )}

      {step.pending && (
        <PermissionBody
          data={step.pending.data}
          requestId={step.pending.requestId}
          busy={busy}
          onDecision={onDecision}
        />
      )}
    </div>
  );
}

function ToolField({ label, value }: { label: string; value: string }) {
  const [expanded, setExpanded] = useState(false);
  // Collapsed keeps the box compact (a short scrollable window) and hard-caps
  // very long blobs so they can't freeze layout — but that also means content
  // past the cap can't be reached by scrolling. Expanding lifts both limits and
  // wraps long lines so the whole input/output is visible on demand.
  const LIMIT = 2000;
  const clipped = value.length > LIMIT || value.split("\n").length > 12;
  const shown = expanded || value.length <= LIMIT ? value : `${value.slice(0, LIMIT)}…`;
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <div className="text-[9px] font-medium uppercase tracking-wide text-muted">{label}</div>
        {clipped && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex items-center gap-0.5 rounded px-1 py-0.5 text-[9px] font-medium uppercase tracking-wide text-muted hover:bg-bg"
            title={expanded ? "Collapse" : "Show full content"}
          >
            <ChevronDown
              size={11}
              className={`transition-transform ${expanded ? "rotate-180" : ""}`}
            />
            {expanded ? "Collapse" : "Expand"}
          </button>
        )}
      </div>
      <pre
        className={`mt-0.5 overflow-auto rounded bg-bg px-2 py-1 font-mono text-[10px] leading-snug ${
          expanded ? "whitespace-pre-wrap break-words" : "max-h-48"
        }`}
      >
        {shown}
      </pre>
    </div>
  );
}

// A searchable topic lookup (combobox), used to associate an agent with a topic.
// Extracted to ./TopicPicker so the Live meeting session picker can reuse it;
// re-exported here to keep existing import sites (AgentSettingsPanel) working.
export { TopicPicker };

// Persisted "Show" toggle preferences for the workflow timeline. One JSON blob
// in localStorage so a user's choices stick across agents and app restarts.
type ShowPrefs = {
  system: boolean;
  assistant: boolean;
  thinking: boolean;
  tool: boolean;
  lifecycle: boolean;
};

const SHOW_PREFS_KEY = "precursor:agent-show-prefs";

const DEFAULT_SHOW_PREFS: ShowPrefs = {
  system: false,
  assistant: true,
  thinking: true,
  tool: true,
  lifecycle: true,
};

function readShowPrefs(): ShowPrefs {
  if (typeof window === "undefined") return DEFAULT_SHOW_PREFS;
  try {
    const raw = window.localStorage.getItem(SHOW_PREFS_KEY);
    if (raw) return { ...DEFAULT_SHOW_PREFS, ...(JSON.parse(raw) as Partial<ShowPrefs>) };
  } catch {
    /* corrupt/unavailable storage — fall back to defaults */
  }
  return DEFAULT_SHOW_PREFS;
}

// A row in the rendered workflow: a centered message node, a grouped tool box,
// or a side hook bubble.
type WorkflowRow =
  | { type: "node"; ev: AgentEvent; cat: "user" | "system" | "assistant" | "reasoning" | "error" }
  | { type: "tool"; step: ToolStep }
  | { type: "hook"; ev: AgentEvent };

// Collapse the raw event stream into renderable rows. Tool start/output and any
// pending approval that share a request_id are merged into one ToolStep box so
// the workflow reads as discrete actions rather than scattered events.
function buildRows(events: AgentEvent[]): WorkflowRow[] {
  const rows: WorkflowRow[] = [];
  const toolIndex = new Map<string, ToolStep>();
  // A tool box is placed in the flow at its *start*, not at whatever event for
  // it arrives first. Interrupted/resumed turns can stream a tool's completion
  // before its start (out of order); without this, the box floats above the
  // assistant message that requested it. Steps seen completion-first are held
  // here until their start/partial arrives, then appended at the end as a
  // fallback if a start never comes.
  const placed = new Set<ToolStep>();
  const unplaced: ToolStep[] = [];

  const pick = (data: Record<string, unknown> | null, ...keys: string[]): string | undefined => {
    if (!data) return undefined;
    for (const k of keys) {
      const v = data[k];
      if (typeof v === "string" && v.trim()) return v;
    }
    return undefined;
  };

  for (const ev of events) {
    const kind = ev.kind.toLowerCase();
    const cat = classify(ev);
    if (cat === "skip") continue;
    // Permission echoes carry no actionable content — drop them as noise.
    if (kind.includes("permissioncompleted") || kind.includes("permissionrequested")) continue;

    const isToolish = cat === "tool" || cat === "permission";
    if (isToolish) {
      const groupKey = ev.request_id ?? `tool-${rows.length}`;
      let step = ev.request_id ? toolIndex.get(groupKey) : undefined;
      // A tool is "done" when its completion event arrives — independent of
      // whether it carried any output text (many tools complete silently).
      // Partial-result events stream interim output but don't finish the tool.
      const isComplete = kind.includes("complete");
      const isFinalResult = kind.includes("result") && !kind.includes("partial");
      const isTerminal = isComplete || isFinalResult;
      if (!step) {
        step = { key: groupKey, toolName: ev.tool_name };
        if (ev.request_id) toolIndex.set(groupKey, step);
        if (isTerminal) {
          // Completion before start → defer placement until the start arrives.
          unplaced.push(step);
        } else {
          rows.push({ type: "tool", step });
          placed.add(step);
        }
      } else if (!placed.has(step) && !isTerminal) {
        // The (late) start/partial for a deferred step — place it in order now.
        rows.push({ type: "tool", step });
        placed.add(step);
      }
      if (ev.tool_name) step.toolName = ev.tool_name;
      const input = pick(ev.data, "arguments", "input");
      if (input) step.input = input;
      if (isComplete || isFinalResult) {
        const output = pick(ev.data, "result", "output") ?? ev.text ?? undefined;
        if (output) step.output = output;
      }
      if (isComplete || isFinalResult) step.done = true;
      if (kind === "permission_request") {
        step.pending = { data: (ev.data ?? {}) as Record<string, unknown>, requestId: ev.request_id };
      }
      continue;
    }

    if (cat === "hook") {
      rows.push({ type: "hook", ev });
    } else if (cat === "assistant" && (!ev.text || !ev.text.trim())) {
      // Streaming emits an empty AssistantMessageStartData marker per message;
      // drop the contentless frames so they don't render as blank "Assistant"
      // steps. The real answer arrives as AssistantMessageData (with text).
      continue;
    } else if (cat === "reasoning") {
      // Streamed reasoning deltas carry no standalone text (the SDK sends the
      // full block separately as AssistantReasoningData); drop the empty ones so
      // streaming mode doesn't fill the timeline with blank "thinking" steps.
      if (!ev.text || !ev.text.trim()) continue;
      // The SDK emits reasoning right AFTER the assistant message of a turn;
      // surface it just before so each turn reads think → speak and a trailing
      // reasoning never dangles below the final answer.
      let at = rows.length;
      while (at > 0 && rows[at - 1].type === "hook") at--;
      const anchor = at > 0 ? rows[at - 1] : undefined;
      if (anchor && anchor.type === "node" && anchor.cat === "assistant") {
        rows.splice(at - 1, 0, { type: "node", ev, cat });
      } else {
        rows.push({ type: "node", ev, cat });
      }
    } else {
      rows.push({ type: "node", ev, cat });
    }
  }
  // Fallback: tools whose start never arrived (truly broken turn) still render,
  // appended in creation order rather than vanishing.
  for (const step of unplaced) {
    if (!placed.has(step)) {
      rows.push({ type: "tool", step });
      placed.add(step);
    }
  }
  return rows;
}

// Derive a per-assistant-answer "generation time" (ms) from event timestamps,
// mirroring the elapsed readout on topic/chat turns. A turn is anchored at its
// `turn_start` (or the user prompt, when the SDK omits the marker); each
// assistant message in that turn is measured from that anchor. Keyed by the
// event object so the timeline can look it up while rendering.
function computeElapsedByEvent(events: AgentEvent[]): Map<AgentEvent, number> {
  const map = new Map<AgentEvent, number>();
  let anchorAt: number | null = null;
  const parse = (at: string | null): number | null => {
    if (!at) return null;
    const t = new Date(at).getTime();
    return Number.isNaN(t) ? null : t;
  };
  for (const ev of events) {
    const kind = ev.kind.toLowerCase();
    const atMs = parse(ev.at);
    if (kind === "turn_start" || classify(ev) === "user") {
      if (atMs != null) anchorAt = atMs;
      continue;
    }
    if (
      classify(ev) === "assistant" &&
      ev.text &&
      ev.text.trim() &&
      atMs != null &&
      anchorAt != null
    ) {
      const delta = atMs - anchorAt;
      if (delta >= 0) map.set(ev, delta);
    }
  }
  return map;
}

// Resolve the concrete model for each assistant answer from its turn's usage
// event. The SDK emits an `AssistantUsageData` (kind "usage") carrying the
// resolved model just before each assistant message in the same turn, so we
// track the latest usage model seen since the turn started and tag each answer
// with it. The call site falls back to the session model when a turn predates
// this capture (older archived runs) or reported no usage.
function computeModelByEvent(events: AgentEvent[]): Map<AgentEvent, string> {
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
    if (classify(ev) === "assistant" && ev.text && ev.text.trim() && currentModel) {
      map.set(ev, currentModel);
    }
  }
  return map;
}

// out of the DOM until the user scrolls toward the top, where another window's
// worth is revealed. Keeps very long agent runs from rendering thousands of
// nodes up front.
const AGENT_SEGMENT_WINDOW = 40;

export function AgentView({
  agents,
  agentId,
  enabled,
  available,
  unavailableReason,
  onReload,
  onSelect,
  onOpenSettings,
  draftTopicId,
}: AgentViewProps) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [me, setMe] = useState<Me | null>(null);
  const [task, setTask] = useState("");
  const [newTopicId, setNewTopicId] = useState<number | null>(null);
  const [followUp, setFollowUp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Optimistic echo of a just-sent prompt, scoped to the agent it targets so it
  // survives the switch into a freshly created session but never leaks onto
  // another. Cleared once the backend records the real user turn.
  const [pending, setPending] = useState<{ agentId: number; text: string } | null>(null);

  // Preselect the originating topic in the new-agent form when opened via
  // "/agent" from a topic (only while no session is selected).
  useEffect(() => {
    if (agentId == null && draftTopicId != null) setNewTopicId(draftTopicId);
  }, [agentId, draftTopicId]);

  // Workflow display toggles, persisted across sessions (localStorage). System
  // (the big base prompt) is noise by default. "Tool" shows/hides whole tool
  // boxes (their I/O stays collapsed, expandable per-box). "Thinking" shows/hides
  // the agent's reasoning steps.
  const [showPrefs, setShowPrefs] = useState<ShowPrefs>(readShowPrefs);
  useEffect(() => {
    try {
      window.localStorage.setItem(SHOW_PREFS_KEY, JSON.stringify(showPrefs));
    } catch {
      /* private mode / quota — prefs just won't persist */
    }
  }, [showPrefs]);
  const toggleShow = (k: keyof ShowPrefs) => setShowPrefs((p) => ({ ...p, [k]: !p[k] }));

  // Shared composer infrastructure (same as topics/chats): resizable height,
  // dictation, ↑/↓ history, and a slash/skills picker. Only one composer is
  // mounted at a time (the start form *or* the follow-up box), so a single
  // speech/skills setup serves both — dictation targets whichever is active.
  const settings = useSettings();
  const selectedRef = useRef(false);
  const { height: composerHeight, onMouseDown: onComposerResize } = useResizableHeight({
    storageKey: "precursor:agent-composer:height",
    defaultHeight: 56,
    min: 40,
    max: 480,
  });
  const [interimText, setInterimText] = useState("");
  const appendFinalChunk = (text: string) => {
    const chunk = text.trim();
    if (!chunk) return;
    const append = (d: string) => (d ? `${d.replace(/\s+$/, "")} ${chunk}` : chunk);
    if (selectedRef.current) setFollowUp(append);
    else setTask(append);
    setInterimText("");
  };
  const speech = useAzureSpeech({
    onFinalChunk: appendFinalChunk,
    onInterim: setInterimText,
    enabled: settings?.stt_azure_ready ?? false,
    lang: settings?.azure_speech_language || undefined,
  });
  useEffect(() => {
    if (!speech.listening) setInterimText("");
  }, [speech.listening]);

  // Agents support only the system-handled slash commands (/rename, /clear,
  // /archive); skills and every other builtin are disabled here. They only apply
  // to an existing session, so the start composer offers none and the follow-up
  // composer offers just those three. The backend rejects anything else.
  const taskSuggestions = useMemo<SlashCommand[]>(() => [], []);
  const followUpSuggestions = useMemo<SlashCommand[]>(
    () => matchAgentSlashCommands(followUp) ?? [],
    [followUp],
  );

  // Autoscroll: keep the newest step in view as the workflow grows, but only
  // while the user is parked at the bottom (don't yank them away mid-scroll).
  const scrollRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  // Reverse-windowing of the rendered timeline: only segments from
  // `windowStart` onward are mounted. The start is a stable absolute index so
  // appending new segments never shifts what's already on screen; scrolling
  // toward the top lowers it to reveal older history.
  const [windowStart, setWindowStart] = useState(0);
  const windowStartRef = useRef(0);
  // Captured scrollHeight from just before revealing older segments, so the
  // layout effect can keep the viewport anchored after they mount.
  const revealAnchorRef = useRef<number | null>(null);
  useEffect(() => {
    windowStartRef.current = windowStart;
  }, [windowStart]);

  // Keep the viewport steady when older segments are revealed at the top.
  useLayoutEffect(() => {
    if (revealAnchorRef.current == null) return;
    const box = scrollRef.current;
    if (box) box.scrollTop += box.scrollHeight - revealAnchorRef.current;
    revealAnchorRef.current = null;
  }, [windowStart]);

  const selected = useMemo(
    () => agents.find((a) => a.id === agentId) ?? null,
    [agents, agentId],
  );
  useEffect(() => {
    selectedRef.current = selected != null;
  }, [selected]);

  // Derive the workflow timeline (segments + trailing hooks + which assistant
  // rows are the real "answers") from the raw events. Memoised so windowing
  // effects can react to the segment count without rebuilding on every render.
  const selectedStatus = selected?.status ?? null;
  const timeline = useMemo(() => {
    const rows = buildRows(events);
    const terminal = ["idle", "completed", "interrupted", "failed", "cancelled"].includes(
      selectedStatus ?? "",
    );
    const answerRows = new Set<WorkflowRow>();
    let lastAssistant: WorkflowRow | null = null;
    for (const r of rows) {
      if (r.type === "node" && r.cat === "assistant") {
        lastAssistant = r;
      } else if (r.type === "hook" && r.ev.kind.toLowerCase().includes("idle")) {
        if (lastAssistant) answerRows.add(lastAssistant);
        lastAssistant = null;
      }
    }
    if (terminal && lastAssistant) answerRows.add(lastAssistant);

    const visible = rows.filter((r) => {
      if (r.type === "hook") return showPrefs.lifecycle;
      if (r.type === "tool") return showPrefs.tool;
      if (r.type !== "node") return true;
      if (r.cat === "system" && !showPrefs.system) return false;
      if (r.cat === "reasoning" && !showPrefs.thinking) return false;
      // Keep answers visible even when assistant chatter is hidden.
      if (r.cat === "assistant" && !showPrefs.assistant && !answerRows.has(r)) return false;
      return true;
    });

    const segments: { row: WorkflowRow; hooks: AgentEvent[] }[] = [];
    let pendingHooks: AgentEvent[] = [];
    for (const r of visible) {
      if (r.type === "hook") {
        pendingHooks.push(r.ev);
        continue;
      }
      segments.push({ row: r, hooks: pendingHooks });
      pendingHooks = [];
    }
    return { segments, trailingHooks: pendingHooks, answerRows };
  }, [events, showPrefs, selectedStatus]);

  // Per-assistant-answer generation time, derived from event timestamps and
  // looked up by event object while rendering each node's metadata line.
  const elapsedByEvent = useMemo(() => computeElapsedByEvent(events), [events]);

  // Per-assistant-answer resolved model (from the turn's usage event), with the
  // session model as fallback at the render site.
  const modelByEvent = useMemo(() => computeModelByEvent(events), [events]);

  // Whether the backend has now recorded the real user turn for our optimistic
  // echo. Computed in render so the echo is dropped in the same paint that shows
  // the real node — no duplicate flashes — while the effect clears the state.
  const pendingLanded = useMemo(() => {
    if (!pending) return false;
    for (let i = events.length - 1; i >= 0; i--) {
      if (classify(events[i]) !== "user") continue;
      return (events[i].text ?? "").trim() === pending.text.trim();
    }
    return false;
  }, [events, pending]);
  useEffect(() => {
    if (pendingLanded) setPending(null);
  }, [pendingLanded]);
  const showPending = pending != null && pending.agentId === agentId && !pendingLanded;

  // Maintain the render window as the timeline grows. While the user is parked
  // at the bottom, keep it bounded to the most recent page so long runs don't
  // accumulate unbounded DOM. While scrolled up, leave the start fixed (only
  // clamped) so newly appended steps never shift the history being read.
  const segmentCount = timeline.segments.length;
  useEffect(() => {
    setWindowStart((start) =>
      pinnedRef.current
        ? Math.max(0, segmentCount - AGENT_SEGMENT_WINDOW)
        : Math.min(start, Math.max(0, segmentCount - 1)),
    );
  }, [segmentCount]);

  const loadEvents = useCallback(async (id: number): Promise<void> => {
    try {
      setEvents(await api.agents.getEvents(id));
    } catch {
      setEvents([]);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    void api.topics.list().then(setTopics).catch(() => setTopics([]));
    void api.me.get().then(setMe).catch(() => setMe(null));
  }, [enabled]);

  // The user's GitHub persona (name + avatar) for the user-prompt node, with a
  // sensible fallback when not signed in.
  const userPersona = useMemo(
    () => ({
      name: me?.github?.name || me?.github?.login || "You",
      avatarUrl: me?.github?.avatar_url ?? null,
    }),
    [me],
  );

  // Prior user turns (initial task + follow-ups) for ↑/↓ history recall.
  const userHistory = useMemo(
    () =>
      events
        .filter((e) => classify(e) === "user")
        .map((e) => e.text ?? "")
        .filter(Boolean),
    [events],
  );

  // Load the selected session's timeline, and keep it live on agent.changed.
  useEffect(() => {
    // Drop an optimistic echo when leaving the agent it targeted (it survives the
    // switch *into* its own freshly created session, cleared later when it lands).
    setPending((p) => (p && p.agentId !== agentId ? null : p));
    if (agentId == null) {
      setEvents([]);
      return;
    }
    void loadEvents(agentId);
    return eventBus.subscribe((ev) => {
      if (ev.type !== "agent.changed") return;
      if (ev.agent_session_id == null || ev.agent_session_id === agentId) {
        void loadEvents(agentId);
      }
    });
  }, [agentId, loadEvents]);

  // Drive the app-global sign-in banner when a turn surfaces an MCP server that
  // needs interactive auth (e.g. WorkIQ OAuth lapsed). The agent runtime emits a
  // synthetic `mcp_auth_required` event; mirror it into the shared store so the
  // user can re-authenticate inline instead of digging through Settings.
  useEffect(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const ev = events[i];
      if (ev.kind !== "mcp_auth_required") continue;
      const server =
        ev.tool_name ?? (ev.data?.server as string | undefined) ?? "workiq";
      mcpAuthStore.report(server, ev.text ?? "Sign-in required.");
      return;
    }
  }, [events]);

  // Snap the scroll container to its absolute bottom. We drive the container's
  // scrollTop directly (rather than scrollIntoView on an anchor) so height that
  // lands late — streaming text, markdown/code reflow, images — can't leave us
  // short of the real bottom.
  const scrollToBottom = useCallback(() => {
    const box = scrollRef.current;
    if (!box) return;
    box.scrollTop = box.scrollHeight;
  }, []);

  // While pinned, stay glued to the bottom as the content height changes. A
  // ResizeObserver catches every reflow (including ones that land after React
  // has already committed the events update), which the events-effect alone
  // kept missing — that was the "not quite reaching the bottom" symptom.
  useEffect(() => {
    const inner = innerRef.current;
    if (!inner) return;
    const ro = new ResizeObserver(() => {
      if (pinnedRef.current) scrollToBottom();
    });
    ro.observe(inner);
    return () => ro.disconnect();
  }, [scrollToBottom]);

  // New steps: follow to the bottom while pinned, once layout has settled.
  useEffect(() => {
    if (!pinnedRef.current) return;
    requestAnimationFrame(scrollToBottom);
  }, [events, showPending, scrollToBottom]);

  // Jump straight to the bottom (and re-pin) when switching agents. The window
  // effect re-bounds `windowStart` to the most recent page once the new
  // timeline loads (pinnedRef is true here).
  useEffect(() => {
    pinnedRef.current = true;
    requestAnimationFrame(scrollToBottom);
  }, [agentId, scrollToBottom]);

  const onScroll = useCallback(() => {
    const box = scrollRef.current;
    if (!box) return;
    pinnedRef.current = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    // Near the top with older segments still hidden: reveal the next window,
    // anchoring the viewport so it doesn't jump as boxes mount above.
    if (box.scrollTop < 120 && windowStartRef.current > 0) {
      revealAnchorRef.current = box.scrollHeight;
      setWindowStart((s) => Math.max(0, s - AGENT_SEGMENT_WINDOW));
    }
  }, []);

  async function startTask(): Promise<void> {
    if (busy || !task.trim()) return;
    const message = task.trim();
    setBusy(true);
    setError(null);
    try {
      const created = await api.agents.create({
        task: message,
        topic_id: newTopicId,
      });
      // Echo the prompt into the new session's transcript right away so the
      // hand-off feels immediate while the runtime spins the agent up.
      setPending({ agentId: created.id, text: message });
      setTask("");
      setNewTopicId(null);
      onReload();
      onSelect(created.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function sendFollowUp(explicit?: string): Promise<void> {
    if (!selected || busy) return;
    const message = (explicit ?? followUp).trim();
    if (!message) return;
    // /clear erases the whole transcript on the backend — confirm first, mirroring
    // the topic/chat clear flow.
    if (
      /^\/clear\b/i.test(message) &&
      !window.confirm("Clear this agent conversation? The transcript will be erased.")
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    // Clear the composer and echo the prompt immediately so it's obvious the
    // message left, even before the backend records the real turn.
    if (explicit === undefined) setFollowUp("");
    setPending({ agentId: selected.id, text: message });
    try {
      await api.agents.send(selected.id, message);
      onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPending(null);
      // Restore the draft so the user doesn't lose what they typed.
      if (explicit === undefined) setFollowUp(message);
    } finally {
      setBusy(false);
    }
  }

  async function stopAgent(): Promise<void> {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await api.agents.cancel(selected.id);
      onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function resume(): Promise<void> {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await api.agents.resume(selected.id);
      onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function approve(
    requestId: string,
    decision: AgentPermissionDecisionValue,
  ): Promise<void> {
    if (!selected) return;
    setBusy(true);
    try {
      await api.agents.resolvePermission(selected.id, requestId, decision);
      onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  // Disabled: send the user to Settings to turn the feature on.
  if (!enabled) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
        <Bot size={28} className="text-muted" />
        <div className="max-w-sm space-y-1">
          <p className="text-sm font-medium">Agents mode is off</p>
          <p className="text-[12px] text-muted">
            Hand long-running tasks to an autonomous Copilot agent, attached to a
            topic or chat. Turn it on in Settings to get started.
          </p>
        </div>
        <button
          type="button"
          onClick={onOpenSettings}
          className="flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-sm text-white"
        >
          <SettingsIcon size={14} /> Open Settings
        </button>
      </div>
    );
  }

  // Enabled but nothing selected: the "start a new task" surface.
  if (!selected) {
    return (
      <div className="mx-auto flex h-full w-full max-w-2xl flex-col justify-center gap-3 p-8">
        <div className="flex items-center gap-2">
          <Bot size={18} />
          <h2 className="text-sm font-medium">Start an agent task</h2>
        </div>
        <p className="text-[12px] text-muted">
          Describe a task to hand off. The agent runs on its own and posts results
          back to a topic or chat when you attach one.
        </p>
        {!available && (
          <div className="rounded border border-amber-500/30 bg-amber-500/10 p-2 text-[11px] text-amber-600 dark:text-amber-400">
            The Copilot runtime isn&apos;t available yet
            {unavailableReason ? `: ${unavailableReason}` : "."}
          </div>
        )}
        <label className="flex items-center gap-2 text-[12px] text-muted">
          Associate with topic
          <TopicPicker
            topics={topics}
            value={newTopicId}
            onChange={setNewTopicId}
            disabled={!available || busy}
          />
        </label>
        {error && <p className="text-[11px] text-red-500">{error}</p>}
        <Composer
          value={task}
          onChange={setTask}
          onSend={() => void startTask()}
          onStop={() => {}}
          streaming={false}
          suggestions={taskSuggestions}
          userHistory={[]}
          speech={speech}
          interimText={interimText}
          height={composerHeight}
          onResizeStart={onComposerResize}
          placeholder="e.g. Investigate the flaky CI test and propose a fix…"
          toolbarStart={<ComposerModelControls variant="agents" />}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 w-full">
      <div className="mx-auto flex h-full min-w-0 w-full max-w-3xl flex-col">
        {/* Scrollable workflow region. */}
        <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto px-5 py-3">
          <div ref={innerRef}>
        {error && <p className="mb-2 text-[11px] text-red-500">{error}</p>}

        {selected.status === "needs_approval" && (
          <div className="mb-3 flex items-center gap-1.5 rounded border border-orange-500/30 bg-orange-500/10 px-2 py-1 text-[11px] text-orange-600 dark:text-orange-400">
            <ShieldQuestion size={13} /> Waiting for your approval — see the highlighted step
            below.
          </div>
        )}

        {selected.status === "interrupted" && (
          <div className="mb-3 flex items-center justify-between gap-2 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-700 dark:text-amber-400">
            <span className="flex items-center gap-1.5">
              <AlertTriangle size={13} /> This turn was interrupted before it
              finished.
            </span>
            {selected.active_prompt && (
              <button
                type="button"
                onClick={() => void resume()}
                disabled={busy}
                className="flex items-center gap-1 rounded bg-amber-500/20 px-2 py-0.5 font-medium text-amber-800 hover:bg-amber-500/30 disabled:opacity-50 dark:text-amber-300"
              >
                <PlayCircle size={12} /> Resume
              </button>
            )}
          </div>
        )}

        {selected.error && (
          <div className="mb-3 rounded border border-red-500/30 bg-red-500/10 p-2 text-[11px] text-red-500">
            {selected.error}
          </div>
        )}

        {(() => {
          const { segments, trailingHooks, answerRows } = timeline;

          if (segments.length === 0 && trailingHooks.length === 0)
            return showPending ? (
              <div className="flex flex-col items-center">
                <div className="flex w-full justify-end">
                  <PendingUserBubble text={pending!.text} user={userPersona} />
                </div>
              </div>
            ) : (
              <p className="text-[11px] text-muted">No steps recorded yet.</p>
            );

          // Window the rendered segments: only those from `windowStart` onward
          // are mounted. `windowStart` is an absolute index, so keys stay stable
          // and appended steps never shift on-screen history; scrolling up
          // lowers it to reveal older boxes.
          const hiddenCount = Math.min(windowStart, segments.length);
          const shownSegments = hiddenCount > 0 ? segments.slice(hiddenCount) : segments;

          return (
            <div className="flex flex-col items-center">
              {hiddenCount > 0 && (
                <p className="mb-2 text-[11px] text-muted">
                  Scroll up to load earlier steps…
                </p>
              )}
              {shownSegments.map((seg, idx) => {
                const absoluteIdx = hiddenCount + idx;
                // Chat-style placement over the workflow spine: the user's own
                // prompts sit to the right, the assistant's answers to the left,
                // and everything else (system, tools, thinking, errors) centered.
                const align =
                  seg.row.type === "node" && seg.row.cat === "user"
                    ? "justify-end"
                    : seg.row.type === "node" && seg.row.cat === "assistant"
                      ? "justify-start"
                      : "justify-center";
                return (
                <Fragment key={absoluteIdx}>
                  {absoluteIdx === 0 ? (
                    seg.hooks.length > 0 && <HookGutter hooks={seg.hooks} />
                  ) : (
                    <StepConnector hooks={seg.hooks} />
                  )}
                  <div className={`flex w-full ${align}`}>
                    {seg.row.type === "tool" ? (
                      <ToolBox
                        step={seg.row.step}
                        busy={busy}
                        onDecision={approve}
                      />
                    ) : seg.row.type === "node" ? (
                      <MessageNode
                        event={seg.row.ev}
                        category={seg.row.cat}
                        isLastAnswer={answerRows.has(seg.row)}
                        user={userPersona}
                        model={modelByEvent.get(seg.row.ev) ?? selected.model ?? null}
                        elapsedMs={elapsedByEvent.get(seg.row.ev) ?? null}
                        onPickSuggestion={(text) => void sendFollowUp(text)}
                        suggestionsDisabled={
                          selected.status === "running" ||
                          selected.status === "pending" ||
                          selected.status === "needs_approval"
                        }
                      />
                    ) : null}
                  </div>
                </Fragment>
                );
              })}
              {trailingHooks.length > 0 && <HookGutter hooks={trailingHooks} />}
              {showPending && (
                <>
                  <StepConnector hooks={[]} />
                  <div className="flex w-full justify-end">
                    <PendingUserBubble text={pending!.text} user={userPersona} />
                  </div>
                </>
              )}
            </div>
          );
        })()}
          </div>
      </div>

      {/* Follow-up: always visible. While a turn is in flight the input is
          disabled and the send button becomes a Stop control, matching the
          topic/chat composer pattern. */}
      {(() => {
        const turnActive =
          selected.status === "running" ||
          selected.status === "pending" ||
          selected.status === "needs_approval";
        // While the send request is still in flight (`busy`) the turn hasn't
        // reported active yet — lock the input so the prompt can't be sent twice,
        // but don't flash a Stop control until there's actually a turn to stop.
        const sending = busy && pending != null;
        return (
          <div className="shrink-0 border-t border-border px-5 py-3">
            <Composer
              value={followUp}
              onChange={setFollowUp}
              onSend={() => void sendFollowUp()}
              onStop={() => void stopAgent()}
              streaming={turnActive}
              disabled={turnActive || sending}
              suggestions={followUpSuggestions}
              userHistory={userHistory}
              speech={speech}
              interimText={interimText}
              height={composerHeight}
              onResizeStart={onComposerResize}
              placeholder={
                turnActive
                  ? "Agent is working… use Stop to interrupt"
                  : sending
                    ? "Sending…"
                    : "Send a follow-up message…"
              }
              toolbarStart={<ComposerModelControls variant="agents" />}
            />
          </div>
        );
      })()}
      </div>
      <AgentInsightsPanel
        showPrefs={showPrefs}
        toggleShow={toggleShow}
        events={events}
        model={selected.model ?? null}
      />
    </div>
  );
}
