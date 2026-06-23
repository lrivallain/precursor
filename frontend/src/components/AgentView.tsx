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
  Pencil,
  Play,
  Send,
  Settings as SettingsIcon,
  ShieldQuestion,
  Sparkles,
  Square,
  Terminal,
  Trash2,
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
import { AgentStatusBadge } from "./AgentStatusBadge";
import { useConfirm } from "./ConfirmDialog";

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

// A minimized "transition" hook (turn start/end, usage, idle…) rendered as a
// small centered pill between boxes rather than a full node.
function HookBadge({ event }: { event: AgentEvent }) {
  return (
    <div className="flex items-center gap-1.5 py-0.5 text-[10px] text-muted">
      <CircleDot size={9} className="opacity-60" />
      <span className="uppercase tracking-wide">{event.kind.replace(/_/g, " ")}</span>
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

// One node in the centered linked-box workflow.
function WorkflowNode({
  event,
  category,
  isLastAnswer,
  showToolDetail,
  busy,
  onDecision,
}: {
  event: AgentEvent;
  category: Exclude<StepCategory, "skip" | "hook">;
  isLastAnswer: boolean;
  showToolDetail: boolean;
  busy: boolean;
  onDecision: DecisionHandler;
}) {
  const isPermission = category === "permission";
  const isTool = category === "tool";
  const style = CATEGORY_STYLE[category];
  const data = (event.data ?? {}) as Record<string, unknown>;
  const [open, setOpen] = useState(false);
  const title =
    isPermission && typeof data.title === "string"
      ? data.title
      : isTool
        ? event.tool_name || style.label
        : style.label;
  const icon = isTool ? toolIcon(event.tool_name) : style.icon;

  // Tool I/O captured by the backend; rendered on demand so the box stays compact.
  const toolDetail = useMemo(() => {
    if (!isTool) return null;
    const parts: { label: string; value: string }[] = [];
    for (const [k, label] of [
      ["arguments", "input"],
      ["input", "input"],
      ["result", "result"],
      ["output", "result"],
    ] as const) {
      const v = data[k];
      if (typeof v === "string" && v.trim()) parts.push({ label, value: v });
    }
    if (event.text && event.text.trim()) parts.push({ label: "result", value: event.text });
    return parts.length ? parts : null;
  }, [isTool, data, event.text]);

  const detailOpen = open || showToolDetail;

  // The real final answer gets an emerald highlight so it's easy to find.
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
          {isTool && event.tool_status === "running" ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            icon
          )}
        </span>
        <span className="text-[11px] font-semibold capitalize">{title}</span>
        {isLastAnswer && (
          <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-emerald-600 dark:text-emerald-400">
            Answer
          </span>
        )}
        {event.tool_status && !isLastAnswer && (
          <span className="text-[10px] text-muted">{event.tool_status}</span>
        )}
        {isTool && toolDetail && (
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
      {isPermission ? (
        <PermissionBody
          data={data}
          requestId={event.request_id}
          busy={busy}
          onDecision={onDecision}
        />
      ) : isTool ? (
        detailOpen &&
        toolDetail && (
          <div className="mt-1.5 space-y-1.5">
            {toolDetail.map((p, i) => (
              <div key={i}>
                <div className="text-[9px] font-medium uppercase tracking-wide text-muted">
                  {p.label}
                </div>
                <pre className="mt-0.5 max-h-48 overflow-auto rounded bg-bg px-2 py-1 font-mono text-[10px] leading-snug">
                  {p.value.length > 2000 ? `${p.value.slice(0, 2000)}…` : p.value}
                </pre>
              </div>
            ))}
          </div>
        )
      ) : (
        event.text && (
          <p className="mt-1 whitespace-pre-wrap text-[11px] text-muted">
            {event.text.length > 1200 ? `${event.text.slice(0, 1200)}…` : event.text}
          </p>
        )
      )}
    </div>
  );
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
  const confirmAction = useConfirm();
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

  // Inline rename.
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState("");

  // Autoscroll: keep the newest step in view as the workflow grows.
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

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

  // Autoscroll to the newest step whenever the timeline grows or the agent
  // switches. Only nudges if the user is already near the bottom, so reading
  // back through history isn't yanked away.
  useEffect(() => {
    const box = scrollRef.current;
    if (!box) return;
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
    if (nearBottom) bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [events]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [agentId]);

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

  async function saveRename(): Promise<void> {
    if (!selected) return;
    const title = nameDraft.trim();
    setRenaming(false);
    if (!title || title === selected.title) return;
    try {
      await api.renameAgent(selected.id, title);
      onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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

  async function cancel(): Promise<void> {
    if (!selected) return;
    setBusy(true);
    try {
      await api.cancelAgent(selected.id);
      onReload();
    } finally {
      setBusy(false);
    }
  }

  async function remove(agent: AgentSession): Promise<void> {
    if (
      !(await confirmAction({
        message: `Delete agent “${agent.title}”? Its session state is discarded.`,
        confirmLabel: "Delete",
        variant: "danger",
      }))
    )
      return;
    setBusy(true);
    try {
      await api.deleteAgent(agent.id);
      onSelect(null);
      onReload();
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
          Attach to topic
          <select
            value={newTopicId ?? ""}
            onChange={(e) => setNewTopicId(e.target.value ? Number(e.target.value) : null)}
            disabled={!available || busy}
            className="rounded border border-border bg-bg px-2 py-1 text-[12px] disabled:opacity-50"
          >
            <option value="">None</option>
            {topics.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title}
              </option>
            ))}
          </select>
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
      {/* Fixed header: title, status and controls stay visible while scrolling. */}
      <div className="shrink-0 border-b border-border px-5 pb-3 pt-5">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              {renaming ? (
                <input
                  autoFocus
                  value={nameDraft}
                  onChange={(e) => setNameDraft(e.target.value)}
                  onBlur={() => void saveRename()}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      void saveRename();
                    } else if (e.key === "Escape") {
                      setRenaming(false);
                    }
                  }}
                  className="min-w-0 flex-1 rounded border border-border bg-bg px-1.5 py-0.5 text-sm font-medium outline-none focus:border-accent"
                />
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setNameDraft(selected.title);
                    setRenaming(true);
                  }}
                  title="Rename agent"
                  className="group flex min-w-0 items-center gap-1.5"
                >
                  <span className="truncate text-sm font-medium">{selected.title}</span>
                  <Pencil
                    size={12}
                    className="shrink-0 text-muted opacity-0 transition group-hover:opacity-100"
                  />
                </button>
              )}
              <AgentStatusBadge status={selected.status} />
            </div>
            <p className="mt-0.5 line-clamp-2 whitespace-pre-wrap text-[11px] text-muted">
              {selected.task_prompt}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={() => {
                setNameDraft(selected.title);
                setRenaming(true);
              }}
              disabled={busy}
              title="Rename"
              className="rounded border border-border p-1.5 text-muted hover:text-fg"
            >
              <Pencil size={14} />
            </button>
            {(selected.status === "running" ||
              selected.status === "pending" ||
              selected.status === "needs_approval") && (
              <button
                type="button"
                onClick={() => void cancel()}
                disabled={busy}
                title="Stop"
                className="rounded border border-border p-1.5 text-muted hover:text-red-500"
              >
                <Square size={14} />
              </button>
            )}
            <button
              type="button"
              onClick={() => void remove(selected)}
              disabled={busy}
              title="Delete"
              className="rounded border border-border p-1.5 text-muted hover:text-red-500"
            >
              <Trash2 size={14} />
            </button>
          </div>
        </div>

        {/* Attach to a topic + workflow display toggles. */}
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11px]">
          <div className="flex items-center gap-1.5">
            <span className="text-muted">Topic</span>
            <select
              value={selected.topic_id ?? ""}
              onChange={(e) => void relink(e.target.value ? Number(e.target.value) : null)}
              disabled={busy}
              className="rounded border border-border bg-bg px-1.5 py-1 text-[11px]"
            >
              <option value="">Nothing</option>
              {topics.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.title}
                </option>
              ))}
            </select>
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
          </div>
        </div>
      </div>

      {/* Scrollable workflow region. */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-3">
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
          const all = events
            .map((ev) => ({ ev, cat: classify(ev) }))
            .filter((s) => s.cat !== "skip");
          // Only the finished turn carries a real "answer" — don't highlight an
          // interim assistant line while the agent is still working.
          const terminal = ["idle", "completed", "interrupted", "failed", "cancelled"].includes(
            selected.status,
          );
          let answerIdx = -1;
          if (terminal) {
            for (let i = all.length - 1; i >= 0; i--) {
              if (all[i].cat === "assistant") {
                answerIdx = i;
                break;
              }
            }
          }
          const visible = all
            .map((s, i) => ({ ...s, isAnswer: i === answerIdx }))
            .filter((s) => {
              if (s.cat === "system" && !showSystem) return false;
              // Keep the final answer even when assistant chatter is hidden.
              if (s.cat === "assistant" && !showAssistant && !s.isAnswer) return false;
              return true;
            });

          if (visible.length === 0)
            return <p className="text-[11px] text-muted">No steps recorded yet.</p>;

          return (
            <div className="flex flex-col items-center">
              {visible.map((s, i) => (
                <Fragment key={i}>
                  {s.cat === "hook" ? (
                    <HookBadge event={s.ev} />
                  ) : (
                    <WorkflowNode
                      event={s.ev}
                      category={s.cat as Exclude<StepCategory, "skip" | "hook">}
                      isLastAnswer={s.isAnswer}
                      showToolDetail={showToolDetail}
                      busy={busy}
                      onDecision={approve}
                    />
                  )}
                  {i < visible.length - 1 && <Connector />}
                </Fragment>
              ))}
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
