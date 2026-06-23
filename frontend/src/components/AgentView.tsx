import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  Brain,
  Check,
  ChevronDown,
  CircleDot,
  Cog,
  FileText,
  Globe,
  Loader2,
  Play,
  Search,
  Send,
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
import type {
  AgentEvent,
  AgentPermissionDecisionValue,
  AgentSession,
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
  if (kind === "permission_request") return "permission";
  if (event.tool_name || kind.includes("tool")) return "tool";
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
    <div className="flex flex-col items-center" aria-hidden>
      <span className="h-3 w-px bg-border" />
      <ChevronDown size={12} className="-my-1 text-border" />
      <span className="h-3 w-px bg-border" />
    </div>
  );
}

// The link between two main boxes, with any lifecycle hooks that happened in
// between floated to the right of the arrow — so the arrow always sits directly
// between the steps and the hooks never push the boxes apart.
function StepConnector({ hooks }: { hooks: AgentEvent[] }) {
  if (hooks.length === 0) return <Connector />;
  return (
    <div className="relative flex w-full max-w-xl justify-end py-1">
      <div
        className="absolute inset-y-0 left-1/2 flex -translate-x-1/2 flex-col items-center"
        aria-hidden
      >
        <span className="w-px flex-1 bg-border" />
        <ChevronDown size={12} className="-my-0.5 shrink-0 text-border" />
        <span className="w-px flex-1 bg-border" />
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
  pending?: { data: Record<string, unknown>; requestId: string | null };
}

// One simple message node (user/system/assistant/reasoning/error).
function MessageNode({
  event,
  category,
  isLastAnswer,
}: {
  event: AgentEvent;
  category: "user" | "system" | "assistant" | "reasoning" | "error";
  isLastAnswer: boolean;
}) {
  const style = CATEGORY_STYLE[category];
  const box = isLastAnswer
    ? "border-emerald-500/50 bg-emerald-500/15 ring-1 ring-emerald-500/30"
    : style.box;
  const marker = isLastAnswer
    ? "border-emerald-500/50 bg-emerald-500/20 text-emerald-600 dark:text-emerald-300"
    : style.marker;
  return (
    <div className={`w-full max-w-xl rounded-lg border p-2.5 ${box}`}>
      <div className="flex items-center gap-2">
        <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full border ${marker}`}>
          {style.icon}
        </span>
        <span className="text-[11px] font-semibold capitalize">{style.label}</span>
        {isLastAnswer && (
          <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-emerald-600 dark:text-emerald-400">
            Answer
          </span>
        )}
      </div>
      {event.text && (
        <p className="mt-1 whitespace-pre-wrap text-[11px] text-muted">
          {event.text.length > 1500 ? `${event.text.slice(0, 1500)}…` : event.text}
        </p>
      )}
    </div>
  );
}

// A grouped tool call. Shows the tool name + status; input/output collapse until
// the user wants to see "what was done"; a pending approval renders inline.
function ToolBox({
  step,
  showToolDetail,
  busy,
  onDecision,
}: {
  step: ToolStep;
  showToolDetail: boolean;
  busy: boolean;
  onDecision: DecisionHandler;
}) {
  const [open, setOpen] = useState(false);
  const detailOpen = open || showToolDetail;
  const hasDetail = Boolean(step.input || step.output);
  const status = step.pending ? "awaiting approval" : step.output !== undefined ? "done" : "running";
  const style = CATEGORY_STYLE.tool;
  const box = step.pending ? CATEGORY_STYLE.permission.box : style.box;
  const marker = step.pending ? CATEGORY_STYLE.permission.marker : style.marker;

  return (
    <div className={`w-full max-w-xl rounded-lg border p-2.5 ${box}`}>
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
  return (
    <div>
      <div className="text-[9px] font-medium uppercase tracking-wide text-muted">{label}</div>
      <pre className="mt-0.5 max-h-48 overflow-auto rounded bg-bg px-2 py-1 font-mono text-[10px] leading-snug">
        {value.length > 2000 ? `${value.slice(0, 2000)}…` : value}
      </pre>
    </div>
  );
}

// A searchable topic lookup (combobox), used to associate an agent with a topic.
function TopicPicker({
  topics,
  value,
  onChange,
  disabled,
}: {
  topics: Topic[];
  value: number | null;
  onChange: (id: number | null) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const current = topics.find((t) => t.id === value) ?? null;

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q ? topics.filter((t) => t.title.toLowerCase().includes(q)) : topics;
    return list.slice(0, 50);
  }, [topics, query]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          setQuery("");
          setOpen((v) => !v);
        }}
        className="flex items-center gap-1 rounded border border-border bg-bg px-2 py-1 text-[11px] disabled:opacity-50"
      >
        <Search size={11} className="text-muted" />
        <span className={current ? "" : "text-muted"}>{current ? current.title : "None"}</span>
        <ChevronDown size={11} className="text-muted" />
      </button>
      {open && (
        <div className="absolute z-20 mt-1 w-60 rounded-md border border-border bg-surface shadow-lg">
          <div className="border-b border-border p-1.5">
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search topics…"
              className="w-full rounded border border-border bg-bg px-2 py-1 text-[11px] outline-none focus:border-accent"
            />
          </div>
          <ul className="max-h-56 overflow-y-auto p-1 text-[11px]">
            <li>
              <button
                type="button"
                onClick={() => {
                  onChange(null);
                  setOpen(false);
                }}
                className={`flex w-full items-center justify-between rounded px-2 py-1 text-left hover:bg-bg ${
                  value === null ? "text-accent" : "text-muted"
                }`}
              >
                None {value === null && <Check size={12} />}
              </button>
            </li>
            {filtered.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  onClick={() => {
                    onChange(t.id);
                    setOpen(false);
                  }}
                  className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left hover:bg-bg ${
                    value === t.id ? "text-accent" : ""
                  }`}
                >
                  <span className="truncate">{t.title}</span>
                  {value === t.id && <Check size={12} className="shrink-0" />}
                </button>
              </li>
            ))}
            {filtered.length === 0 && (
              <li className="px-2 py-1.5 text-muted">No matching topics.</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
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
      if (!step) {
        step = { key: groupKey, toolName: ev.tool_name };
        if (ev.request_id) toolIndex.set(groupKey, step);
        rows.push({ type: "tool", step });
      }
      if (ev.tool_name) step.toolName = ev.tool_name;
      const input = pick(ev.data, "arguments", "input");
      if (input) step.input = input;
      // Output comes only from the tool-complete event (or its text body).
      if (kind.includes("complete") || kind.includes("result")) {
        const output = pick(ev.data, "result", "output") ?? ev.text ?? undefined;
        if (output) step.output = output;
      }
      if (kind === "permission_request") {
        step.pending = { data: (ev.data ?? {}) as Record<string, unknown>, requestId: ev.request_id };
      }
      continue;
    }

    if (cat === "hook") {
      rows.push({ type: "hook", ev });
    } else {
      rows.push({ type: "node", ev, cat });
    }
  }
  return rows;
}

export function AgentView({
  agents,
  agentId,
  enabled,
  available,
  unavailableReason,
  onReload,
  onSelect,
  onOpenSettings,
}: AgentViewProps) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [task, setTask] = useState("");
  const [newTopicId, setNewTopicId] = useState<number | null>(null);
  const [followUp, setFollowUp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Workflow display toggles. System (the big base prompt) is noise by default;
  // tool I/O is collapsed until the user wants to see "what was done".
  const [showSystem, setShowSystem] = useState(false);
  const [showAssistant, setShowAssistant] = useState(true);
  const [showToolDetail, setShowToolDetail] = useState(false);
  // Yellow side bubbles: turn start/end, usage, idle… side info, on by default.
  const [showLifecycle, setShowLifecycle] = useState(true);

  // Autoscroll: keep the newest step in view as the workflow grows, but only
  // while the user is parked at the bottom (don't yank them away mid-scroll).
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);

  const selected = useMemo(
    () => agents.find((a) => a.id === agentId) ?? null,
    [agents, agentId],
  );

  const loadEvents = useCallback(async (id: number): Promise<void> => {
    try {
      setEvents(await api.getAgentEvents(id));
    } catch {
      setEvents([]);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    void api.listTopics().then(setTopics).catch(() => setTopics([]));
  }, [enabled]);

  // Load the selected session's timeline, and keep it live on agent.changed.
  useEffect(() => {
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

  // Autoscroll to the newest step whenever the timeline grows, but only while
  // the user is pinned to the bottom (tracked by the scroll handler). Reading
  // back through history is never interrupted.
  useEffect(() => {
    if (pinnedRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [events]);

  // Jump straight to the bottom (and re-pin) when switching agents.
  useEffect(() => {
    pinnedRef.current = true;
    requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ block: "end" }));
  }, [agentId]);

  const onScroll = useCallback(() => {
    const box = scrollRef.current;
    if (!box) return;
    pinnedRef.current = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  }, []);

  async function startTask(): Promise<void> {
    if (!task.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.createAgent({
        task: task.trim(),
        topic_id: newTopicId,
      });
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

  async function sendFollowUp(): Promise<void> {
    if (!selected || !followUp.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.sendToAgent(selected.id, followUp.trim());
      setFollowUp("");
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
      await api.resolveAgentPermission(selected.id, requestId, decision);
      onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function relink(topicId: number | null): Promise<void> {
    if (!selected) return;
    setBusy(true);
    try {
      await api.linkAgent(selected.id, { topic_id: topicId, chat_id: null });
      onReload();
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
        <textarea
          value={task}
          onChange={(e) => setTask(e.target.value)}
          placeholder="e.g. Investigate the flaky CI test and propose a fix…"
          rows={4}
          disabled={!available || busy}
          className="resize-none rounded border border-border bg-surface px-3 py-2 text-sm disabled:opacity-50"
        />
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
        <div className="flex justify-end">
          <button
            type="button"
            onClick={() => void startTask()}
            disabled={!available || busy || !task.trim()}
            className="flex items-center gap-1.5 rounded bg-accent px-3 py-2 text-sm text-white disabled:opacity-50"
          >
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} Start
            agent
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col">
      {/* Slim sub-toolbar: topic association + workflow display toggles. The
          agent title and stop/delete controls live in the shared app header. */}
      <div className="shrink-0 border-b border-border px-5 pb-2.5 pt-3">
        {selected.task_prompt && (
          <p className="mb-2 line-clamp-2 whitespace-pre-wrap text-[11px] text-muted">
            {selected.task_prompt}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11px]">
          <div className="flex items-center gap-1.5">
            <span className="text-muted">Topic</span>
            <TopicPicker
              topics={topics}
              value={selected.topic_id}
              onChange={(id) => void relink(id)}
              disabled={busy}
            />
          </div>
          <div className="flex items-center gap-1">
            <span className="text-muted">Show</span>
            <ToggleChip active={showSystem} onClick={() => setShowSystem((v) => !v)} label="System" />
            <ToggleChip
              active={showAssistant}
              onClick={() => setShowAssistant((v) => !v)}
              label="Assistant"
            />
            <ToggleChip
              active={showToolDetail}
              onClick={() => setShowToolDetail((v) => !v)}
              label="Tool details"
            />
            <ToggleChip
              active={showLifecycle}
              onClick={() => setShowLifecycle((v) => !v)}
              label="Lifecycle"
            />
          </div>
        </div>
      </div>

      {/* Scrollable workflow region. */}
      <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto px-5 py-3">
        {error && <p className="mb-2 text-[11px] text-red-500">{error}</p>}

        {selected.status === "needs_approval" && (
          <div className="mb-3 flex items-center gap-1.5 rounded border border-orange-500/30 bg-orange-500/10 px-2 py-1 text-[11px] text-orange-600 dark:text-orange-400">
            <ShieldQuestion size={13} /> Waiting for your approval — see the highlighted step
            below.
          </div>
        )}

        {selected.error && (
          <div className="mb-3 rounded border border-red-500/30 bg-red-500/10 p-2 text-[11px] text-red-500">
            {selected.error}
          </div>
        )}

        {(() => {
          const rows = buildRows(events);
          // Only the finished turn carries a real "answer" — don't highlight an
          // interim assistant line while the agent is still working.
          const terminal = ["idle", "completed", "interrupted", "failed", "cancelled"].includes(
            selected.status,
          );
          let answerRow: WorkflowRow | null = null;
          if (terminal) {
            for (let i = rows.length - 1; i >= 0; i--) {
              const r = rows[i];
              if (r.type === "node" && r.cat === "assistant") {
                answerRow = r;
                break;
              }
            }
          }

          const visible = rows.filter((r) => {
            if (r.type === "hook") return showLifecycle;
            if (r.type !== "node") return true;
            if (r.cat === "system" && !showSystem) return false;
            // Keep the final answer even when assistant chatter is hidden.
            if (r.cat === "assistant" && !showAssistant && r !== answerRow) return false;
            return true;
          });

          // Group each main box with the hooks that preceded it, so the arrow
          // sits directly between boxes and hooks float beside it.
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
          const trailingHooks = pendingHooks;

          if (segments.length === 0 && trailingHooks.length === 0)
            return <p className="text-[11px] text-muted">No steps recorded yet.</p>;

          return (
            <div className="flex flex-col items-center">
              {segments.map((seg, idx) => (
                <Fragment key={idx}>
                  {idx === 0 ? (
                    seg.hooks.length > 0 && <HookGutter hooks={seg.hooks} />
                  ) : (
                    <StepConnector hooks={seg.hooks} />
                  )}
                  {seg.row.type === "tool" ? (
                    <ToolBox
                      step={seg.row.step}
                      showToolDetail={showToolDetail}
                      busy={busy}
                      onDecision={approve}
                    />
                  ) : seg.row.type === "node" ? (
                    <MessageNode
                      event={seg.row.ev}
                      category={seg.row.cat}
                      isLastAnswer={seg.row === answerRow}
                    />
                  ) : null}
                </Fragment>
              ))}
              {trailingHooks.length > 0 && <HookGutter hooks={trailingHooks} />}
            </div>
          );
        })()}
        <div ref={bottomRef} />
      </div>

      {/* Follow-up */}
      {(selected.status === "idle" ||
        selected.status === "completed" ||
        selected.status === "interrupted") && (
        <div className="flex shrink-0 items-center gap-2 border-t border-border px-5 py-3">
          <input
            value={followUp}
            onChange={(e) => setFollowUp(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void sendFollowUp();
              }
            }}
            placeholder="Send a follow-up message…"
            disabled={!available || busy}
            className="flex-1 rounded border border-border bg-bg px-3 py-2 text-[12px] disabled:opacity-50"
          />
          <button
            type="button"
            onClick={() => void sendFollowUp()}
            disabled={!available || busy || !followUp.trim()}
            className="flex items-center gap-1 rounded bg-accent px-3 py-2 text-[12px] text-white disabled:opacity-50"
          >
            <Send size={14} />
          </button>
        </div>
      )}
    </div>
  );
}
