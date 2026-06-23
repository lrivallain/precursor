import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  Check,
  Loader2,
  Play,
  Send,
  ShieldQuestion,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { api } from "../lib/api";
import { eventBus } from "../lib/events";
import { settingsStore, useSettings } from "../lib/settingsStore";
import type {
  AgentEvent,
  AgentSession,
  AgentStatus,
  Topic,
} from "../lib/types";
import { useConfirm } from "./ConfirmDialog";

const STATUS_STYLES: Record<AgentStatus, string> = {
  pending: "bg-amber-500/15 text-amber-500",
  running: "bg-sky-500/15 text-sky-500",
  idle: "bg-emerald-500/15 text-emerald-500",
  needs_approval: "bg-orange-500/15 text-orange-500",
  completed: "bg-emerald-500/15 text-emerald-500",
  failed: "bg-red-500/15 text-red-500",
  cancelled: "bg-muted/20 text-muted",
  interrupted: "bg-red-500/15 text-red-500",
};

const STATUS_LABEL: Record<AgentStatus, string> = {
  pending: "Pending",
  running: "Running",
  idle: "Idle",
  needs_approval: "Needs approval",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
};

function StatusBadge({ status }: { status: AgentStatus }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium ${STATUS_STYLES[status]}`}
    >
      {(status === "running" || status === "pending") && (
        <Loader2 size={10} className="animate-spin" />
      )}
      {STATUS_LABEL[status]}
    </span>
  );
}

function relativeTime(iso: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  const mins = Math.round(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return new Date(iso).toLocaleDateString();
}

// One step in the workflow timeline.
function TimelineStep({ event }: { event: AgentEvent }) {
  const isTool = !!event.tool_name;
  const dotColor =
    event.tool_status === "error"
      ? "bg-red-500"
      : event.tool_status === "done"
        ? "bg-emerald-500"
        : event.tool_status === "running"
          ? "bg-sky-500"
          : "bg-border";
  const label = isTool
    ? event.tool_name
    : event.kind.replace(/_/g, " ");
  return (
    <li className="relative pl-5">
      <span
        className={`absolute left-0 top-1.5 h-2 w-2 rounded-full ${dotColor}`}
        aria-hidden
      />
      <div className="flex items-baseline gap-2">
        <span className="text-[11px] font-medium capitalize">{label}</span>
        {event.tool_status && (
          <span className="text-[10px] text-muted">{event.tool_status}</span>
        )}
      </div>
      {event.text && (
        <p className="mt-0.5 whitespace-pre-wrap text-[11px] text-muted">
          {event.text.length > 600 ? `${event.text.slice(0, 600)}…` : event.text}
        </p>
      )}
    </li>
  );
}

function EnableGate({ available, reason }: { available: boolean; reason: string | null }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function enable(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.updateSettings({ agents_enabled: true });
      settingsStore.set(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded border border-border bg-surface p-4 text-sm">
      <div className="flex items-center gap-2 font-medium">
        <Bot size={16} /> Agents mode
      </div>
      <p className="mt-2 text-[12px] text-muted">
        Run long-running, autonomous Copilot agent tasks on demand. An agent can
        be attached to a topic or chat, read its context, and post results back
        when it finishes — without blocking your conversation.
      </p>
      {!available && (
        <p className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 p-2 text-[11px] text-amber-600 dark:text-amber-400">
          The Copilot runtime isn&apos;t available yet
          {reason ? `: ${reason}` : "."} You can still enable the feature; agent
          tasks become runnable once the runtime is installed.
        </p>
      )}
      {error && <p className="mt-2 text-[11px] text-red-500">{error}</p>}
      <button
        type="button"
        onClick={() => void enable()}
        disabled={busy}
        className="mt-3 flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-sm text-white disabled:opacity-50"
      >
        {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
        Enable Agents mode
      </button>
    </div>
  );
}

export function AgentsTab() {
  const settings = useSettings();
  const confirmAction = useConfirm();
  const [agents, setAgents] = useState<AgentSession[]>([]);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [task, setTask] = useState("");
  const [followUp, setFollowUp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectedIdRef = useRef<number | null>(null);
  selectedIdRef.current = selectedId;

  const available = settings?.agents_available ?? false;
  const enabled = settings?.agents_enabled ?? false;

  const loadAgents = useCallback(async (): Promise<AgentSession[]> => {
    const list = await api.listAgents();
    setAgents(list);
    return list;
  }, []);

  const loadEvents = useCallback(async (id: number): Promise<void> => {
    try {
      setEvents(await api.getAgentEvents(id));
    } catch {
      setEvents([]);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    void loadAgents();
    void api.listTopics().then(setTopics).catch(() => setTopics([]));
  }, [enabled, loadAgents]);

  // Live updates: any agent.changed signal refreshes the list, and the selected
  // session's timeline.
  useEffect(() => {
    if (!enabled) return;
    return eventBus.subscribe((ev) => {
      if (ev.type !== "agent.changed") return;
      void loadAgents();
      const current = selectedIdRef.current;
      if (current != null && (ev.agent_session_id == null || ev.agent_session_id === current)) {
        void loadEvents(current);
      }
    });
  }, [enabled, loadAgents, loadEvents]);

  const selected = useMemo(
    () => agents.find((a) => a.id === selectedId) ?? null,
    [agents, selectedId],
  );

  function select(id: number): void {
    setSelectedId(id);
    void loadEvents(id);
  }

  async function startTask(): Promise<void> {
    if (!task.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.createAgent({ task: task.trim() });
      setTask("");
      await loadAgents();
      select(created.id);
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
      await loadAgents();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function approve(
    id: string,
    decision: "approve-once" | "approve-always" | "deny",
  ): Promise<void> {
    if (!selected) return;
    setBusy(true);
    try {
      await api.resolveAgentPermission(selected.id, id, decision);
      await loadAgents();
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
      await loadAgents();
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
      if (selectedId === agent.id) {
        setSelectedId(null);
        setEvents([]);
      }
      await loadAgents();
    } finally {
      setBusy(false);
    }
  }

  async function relink(topicId: number | null): Promise<void> {
    if (!selected) return;
    setBusy(true);
    try {
      await api.linkAgent(selected.id, { topic_id: topicId, chat_id: null });
      await loadAgents();
    } finally {
      setBusy(false);
    }
  }

  // The most recent step carrying a permission request id, used to resolve a
  // parked approval from the timeline.
  const pendingRequestId = useMemo(() => {
    if (selected?.status !== "needs_approval") return null;
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].request_id) return events[i].request_id;
    }
    return null;
  }, [events, selected?.status]);

  if (!enabled) {
    return (
      <section className="space-y-3">
        <EnableGate available={available} reason={settings?.agents_unavailable_reason ?? null} />
      </section>
    );
  }

  return (
    <section className="space-y-3">
      <div className="flex items-start gap-2">
        <textarea
          value={task}
          onChange={(e) => setTask(e.target.value)}
          placeholder={
            available
              ? "Describe a task to hand off to an agent…"
              : "Runtime unavailable — install the Copilot runtime to run tasks."
          }
          rows={2}
          disabled={!available || busy}
          className="flex-1 resize-none rounded border border-border bg-surface px-2 py-1.5 text-sm disabled:opacity-50"
        />
        <button
          type="button"
          onClick={() => void startTask()}
          disabled={!available || busy || !task.trim()}
          className="flex items-center gap-1.5 rounded bg-accent px-3 py-2 text-sm text-white disabled:opacity-50"
        >
          <Play size={14} /> Start
        </button>
      </div>

      {error && <p className="text-[11px] text-red-500">{error}</p>}

      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] gap-3">
        {/* Session list */}
        <ul className="space-y-1.5">
          {agents.length === 0 && (
            <li className="text-[11px] text-muted">No agent sessions yet.</li>
          )}
          {agents.map((a) => (
            <li key={a.id}>
              <button
                type="button"
                onClick={() => select(a.id)}
                className={`w-full rounded border px-2.5 py-2 text-left ${
                  selectedId === a.id
                    ? "border-accent bg-accent/5"
                    : "border-border bg-surface hover:border-accent/40"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[12px] font-medium">{a.title}</span>
                  <StatusBadge status={a.status} />
                </div>
                <div className="mt-1 flex items-center gap-2 text-[10px] text-muted">
                  <span>{relativeTime(a.last_activity_at ?? a.created_at)}</span>
                  {a.topic_id != null && <span>· topic #{a.topic_id}</span>}
                </div>
              </button>
            </li>
          ))}
        </ul>

        {/* Detail / workflow timeline */}
        <div className="rounded border border-border bg-surface p-3">
          {!selected ? (
            <p className="text-[11px] text-muted">Select a session to inspect its workflow.</p>
          ) : (
            <div className="space-y-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium">{selected.title}</span>
                    <StatusBadge status={selected.status} />
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
                      className="rounded border border-border p-1 text-muted hover:text-red-500"
                    >
                      <Square size={13} />
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => void remove(selected)}
                    disabled={busy}
                    title="Delete"
                    className="rounded border border-border p-1 text-muted hover:text-red-500"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>

              {/* Attach to topic */}
              <div className="flex items-center gap-2 text-[11px]">
                <span className="text-muted">Attached to</span>
                <select
                  value={selected.topic_id ?? ""}
                  onChange={(e) =>
                    void relink(e.target.value ? Number(e.target.value) : null)
                  }
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

              {/* Permission prompt */}
              {selected.status === "needs_approval" && pendingRequestId && (
                <div className="rounded border border-orange-500/30 bg-orange-500/10 p-2">
                  <div className="flex items-center gap-1.5 text-[11px] font-medium text-orange-600 dark:text-orange-400">
                    <ShieldQuestion size={13} /> The agent needs permission to continue.
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <button
                      type="button"
                      onClick={() => void approve(pendingRequestId, "approve-once")}
                      disabled={busy}
                      className="rounded bg-accent px-2 py-1 text-[11px] text-white disabled:opacity-50"
                    >
                      Approve once
                    </button>
                    <button
                      type="button"
                      onClick={() => void approve(pendingRequestId, "approve-always")}
                      disabled={busy}
                      className="rounded border border-border px-2 py-1 text-[11px] disabled:opacity-50"
                    >
                      Approve for session
                    </button>
                    <button
                      type="button"
                      onClick={() => void approve(pendingRequestId, "deny")}
                      disabled={busy}
                      className="flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] text-red-500 disabled:opacity-50"
                    >
                      <X size={12} /> Deny
                    </button>
                  </div>
                </div>
              )}

              {/* Result summary */}
              {selected.result_summary && (
                <div className="rounded border border-border bg-bg p-2 text-[11px] whitespace-pre-wrap">
                  {selected.result_summary}
                </div>
              )}
              {selected.error && (
                <div className="rounded border border-red-500/30 bg-red-500/10 p-2 text-[11px] text-red-500">
                  {selected.error}
                </div>
              )}

              {/* Workflow timeline */}
              <div>
                <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-muted">
                  Workflow
                </div>
                {events.length === 0 ? (
                  <p className="text-[11px] text-muted">No steps recorded yet.</p>
                ) : (
                  <ul className="space-y-2 border-l border-border pl-1">
                    {events.map((ev, i) => (
                      <TimelineStep key={i} event={ev} />
                    ))}
                  </ul>
                )}
              </div>

              {/* Follow-up */}
              {(selected.status === "idle" ||
                selected.status === "completed" ||
                selected.status === "interrupted") && (
                <div className="flex items-center gap-2">
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
                    className="flex-1 rounded border border-border bg-bg px-2 py-1.5 text-[12px] disabled:opacity-50"
                  />
                  <button
                    type="button"
                    onClick={() => void sendFollowUp()}
                    disabled={!available || busy || !followUp.trim()}
                    className="flex items-center gap-1 rounded bg-accent px-2.5 py-1.5 text-[12px] text-white disabled:opacity-50"
                  >
                    <Send size={13} />
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
