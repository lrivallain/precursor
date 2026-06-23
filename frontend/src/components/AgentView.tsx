import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bot,
  CircleDot,
  FileText,
  Globe,
  Loader2,
  MessageSquare,
  Play,
  Send,
  Settings as SettingsIcon,
  ShieldQuestion,
  Square,
  Terminal,
  Trash2,
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

// Pick a node icon + accent for a workflow step.
function nodeVisual(event: AgentEvent): { icon: React.ReactNode; ring: string } {
  if (event.kind === "permission_request")
    return {
      icon: <ShieldQuestion size={13} />,
      ring: "border-orange-500/50 bg-orange-500/10 text-orange-600 dark:text-orange-400",
    };
  if (event.tool_name) {
    if (event.tool_status === "error")
      return { icon: <Wrench size={13} />, ring: "border-red-500/50 bg-red-500/10 text-red-500" };
    if (event.tool_status === "running")
      return {
        icon: <Loader2 size={13} className="animate-spin" />,
        ring: "border-sky-500/50 bg-sky-500/10 text-sky-500",
      };
    const name = event.tool_name.toLowerCase();
    const icon = name.includes("shell") ? (
      <Terminal size={13} />
    ) : name.includes("write") || name.includes("file") || name.includes("read") ? (
      <FileText size={13} />
    ) : name.includes("url") || name.includes("fetch") ? (
      <Globe size={13} />
    ) : (
      <Wrench size={13} />
    );
    return {
      icon,
      ring: "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    };
  }
  if (event.kind === "message")
    return { icon: <MessageSquare size={13} />, ring: "border-border bg-surface text-muted" };
  return { icon: <CircleDot size={13} />, ring: "border-border bg-surface text-muted" };
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

// One node in the linked-boxes workflow.
function WorkflowNode({
  event,
  isLast,
  busy,
  onDecision,
}: {
  event: AgentEvent;
  isLast: boolean;
  busy: boolean;
  onDecision: DecisionHandler;
}) {
  const isPermission = event.kind === "permission_request";
  const { icon, ring } = nodeVisual(event);
  const data = (event.data ?? {}) as Record<string, unknown>;
  const title =
    isPermission && typeof data.title === "string"
      ? data.title
      : event.tool_name || event.kind.replace(/_/g, " ");

  return (
    <li className="relative flex gap-3">
      {/* Rail: node marker + connector to the next box. */}
      <div className="flex flex-col items-center">
        <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full border ${ring}`}>
          {icon}
        </span>
        {!isLast && <span className="w-px flex-1 bg-border" />}
      </div>
      {/* Box */}
      <div
        className={`mb-2 flex-1 rounded-lg border p-2 ${
          isPermission ? "border-orange-500/40 bg-orange-500/5" : "border-border bg-surface"
        }`}
      >
        <div className="flex items-baseline gap-2">
          <span className="text-[11px] font-medium capitalize">{title}</span>
          {event.tool_status && (
            <span className="text-[10px] text-muted">{event.tool_status}</span>
          )}
        </div>
        {isPermission ? (
          <PermissionBody
            data={data}
            requestId={event.request_id}
            busy={busy}
            onDecision={onDecision}
          />
        ) : (
          event.text && (
            <p className="mt-0.5 whitespace-pre-wrap text-[11px] text-muted">
              {event.text.length > 800 ? `${event.text.slice(0, 800)}…` : event.text}
            </p>
          )
        )}
      </div>
    </li>
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
  const [followUp, setFollowUp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  async function startTask(): Promise<void> {
    if (!task.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.createAgent({ task: task.trim() });
      setTask("");
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
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col gap-3 overflow-y-auto p-5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{selected.title}</span>
            <AgentStatusBadge status={selected.status} />
          </div>
          <p className="mt-0.5 whitespace-pre-wrap text-[11px] text-muted">
            {selected.task_prompt}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {(selected.status === "running" || selected.status === "pending") && (
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

      {/* Attach to a topic */}
      <div className="flex items-center gap-2 text-[11px]">
        <span className="text-muted">Attached to</span>
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

      {error && <p className="text-[11px] text-red-500">{error}</p>}

      {selected.status === "needs_approval" && (
        <div className="flex items-center gap-1.5 rounded border border-orange-500/30 bg-orange-500/10 px-2 py-1 text-[11px] text-orange-600 dark:text-orange-400">
          <ShieldQuestion size={13} /> Waiting for your approval — see the highlighted step in
          the workflow below.
        </div>
      )}

      {/* Result / error */}
      {selected.result_summary && (
        <div className="whitespace-pre-wrap rounded border border-border bg-surface p-2 text-[11px]">
          {selected.result_summary}
        </div>
      )}
      {selected.error && (
        <div className="rounded border border-red-500/30 bg-red-500/10 p-2 text-[11px] text-red-500">
          {selected.error}
        </div>
      )}

      {/* Workflow */}
      <div>
        <div className="mb-2 text-[10px] font-medium uppercase tracking-wide text-muted">
          Workflow
        </div>
        {events.length === 0 ? (
          <p className="text-[11px] text-muted">No steps recorded yet.</p>
        ) : (
          <ol className="flex flex-col">
            {events.map((ev, i) => (
              <WorkflowNode
                key={i}
                event={ev}
                isLast={i === events.length - 1}
                busy={busy}
                onDecision={approve}
              />
            ))}
          </ol>
        )}
      </div>

      {/* Follow-up */}
      {(selected.status === "idle" ||
        selected.status === "completed" ||
        selected.status === "interrupted") && (
        <div className="mt-auto flex items-center gap-2 pt-2">
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
