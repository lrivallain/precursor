import { useEffect, useMemo, useState } from "react";
import {
  Archive,
  CalendarClock,
  Coins,
  Download,
  Play,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Workflow as WorkflowIcon,
  X,
} from "lucide-react";
import { api } from "../lib/api";
import type {
  AgentApprovalPolicy,
  AgentSession,
  Collection,
  Topic,
  WorkflowSummary,
} from "../lib/types";
import { useConfirm } from "./ConfirmDialog";
import { useSettings } from "../lib/settingsStore";
import { RefineTextarea } from "./RefineTextarea";
import { AgentStatusBadge } from "./AgentStatusBadge";
import { TopicPicker } from "./AgentView";
import { Select } from "./Select";
import { APPROVAL_POLICIES } from "./AgentsSettings";
import {
  defaultRecurrence,
  recurrenceFromSchedule,
  recurrenceToPayload,
  RecurrenceEditor,
  type RecurrenceValue,
} from "./RecurrenceEditor";

interface Props {
  agent: AgentSession;
  onClose: () => void;
  onSaved: (agent: AgentSession) => void;
  onArchived: () => void;
  /** Jump to a workflow that uses this agent. */
  onOpenWorkflow?: (workflowId: number) => void;
  onDeleted: () => void;
}

// Mirrors ChatSettingsPanel (drawer + Title + footer Save + destructive
// archive/delete) so an agent session feels like the same surface as a topic or
// chat. Agent-specific bits: the editable task prompt (its "instructions") and
// the associated-topic picker. Renaming and linking reuse the same endpoints
// the header/timeline already drive. Editing the task re-establishes the SDK
// session server-side so the new instructions actually take effect.
export function AgentSettingsPanel({
  agent,
  onClose,
  onSaved,
  onArchived,
  onDeleted,
  onOpenWorkflow,
}: Props) {
  // Which pipelines reference this agent. Null while loading.
  const [workflows, setWorkflows] = useState<WorkflowSummary[] | null>(null);
  const [showWorkflows, setShowWorkflows] = useState(false);

  useEffect(() => {
    setWorkflows(null);
    setShowWorkflows(false);
    void api.agents
      .workflows(agent.id)
      .then(setWorkflows)
      .catch(() => setWorkflows([]));
  }, [agent.id]);

  const confirmAction = useConfirm();
  const settings = useSettings();
  const [title, setTitle] = useState(agent.title);
  const [task, setTask] = useState(agent.task_prompt);
  const [topicId, setTopicId] = useState<number | null>(agent.topic_id);
  // Per-agent approval-policy override; "" = inherit the global default.
  const [approvalPolicy, setApprovalPolicy] = useState<AgentApprovalPolicy | "">(
    agent.approval_policy ?? "",
  );
  const globalPolicyLabel = useMemo(() => {
    const found = APPROVAL_POLICIES.find((p) => p.value === settings?.agents_approval_policy);
    return found ? found.label.split("—")[0].trim() : "";
  }, [settings?.agents_approval_policy]);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  // Governance: token ceiling (empty = ungoverned) and auto-retry budget.
  const [tokenBudget, setTokenBudget] = useState<string>(
    agent.token_budget != null ? String(agent.token_budget) : "",
  );
  const [maxRetries, setMaxRetries] = useState<number>(agent.max_retries ?? 0);
  const [saving, setSaving] = useState(false);
  // True only while the in-flight save is a "Save & run" (so the two footer
  // buttons can show distinct busy labels).
  const [savingRun, setSavingRun] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Schedule: re-run this agent's task on a cadence (mirrors scheduled topics).
  // A row exists when `agent.schedule` is non-null; the master toggle maps to
  // its `enabled` flag. Recurrence/clear-context are seeded from the embedded
  // summary so reopening shows the live config.
  const hasSchedule = agent.schedule !== null;
  const [scheduleOn, setScheduleOn] = useState<boolean>(agent.schedule?.enabled ?? false);
  const [recurrence, setRecurrence] = useState<RecurrenceValue>(
    agent.schedule ? recurrenceFromSchedule(agent.schedule) : defaultRecurrence(),
  );
  const [clearContext, setClearContext] = useState<boolean>(
    agent.schedule?.clear_context ?? true,
  );
  const [scheduleBusy, setScheduleBusy] = useState(false);

  // The task can't be replayed while a turn is in flight; the server rejects it.
  const taskLocked = ["pending", "running", "needs_approval"].includes(agent.status);
  // POST /{id}/start rejects an already-active agent, so "Save & run" is only
  // offered when the agent isn't mid-turn.
  const isActive = ["pending", "running", "needs_approval", "interrupted"].includes(agent.status);

  useEffect(() => {
    void api.topics.list()
      .then(setTopics)
      .catch(() => setTopics([]));
    void api.collections.list()
      .then(setCollections)
      .catch(() => setCollections([]));
  }, []);

  // Persist every edited field. `run` additionally launches the objective once
  // the save lands (the "Save & run" button): a plain Save is *only* a save —
  // editing the task primes the new instructions but never starts a turn.
  async function save(options?: { run?: boolean }): Promise<void> {
    const run = options?.run ?? false;
    const trimmedTitle = title.trim();
    const trimmedTask = task.trim();
    if (!trimmedTitle || saving) return;
    setSaving(true);
    setSavingRun(run);
    setError(null);
    try {
      const patch: {
        title?: string;
        task?: string;
        approval_policy?: AgentApprovalPolicy | null;
        token_budget?: number | null;
        max_retries?: number;
      } = {};
      if (trimmedTitle !== agent.title) patch.title = trimmedTitle;
      if (trimmedTask && trimmedTask !== agent.task_prompt) patch.task = trimmedTask;
      // Send when changed, including a reset to inherit (null). model_fields_set
      // on the backend distinguishes "omitted" from an explicit null.
      const nextPolicy = approvalPolicy || null;
      if (nextPolicy !== (agent.approval_policy ?? null)) patch.approval_policy = nextPolicy;
      // Governance: empty budget field clears the ceiling (null); a parsed
      // integer sets it. Retries send whenever the number changed.
      const trimmedBudget = tokenBudget.trim();
      const nextBudget = trimmedBudget === "" ? null : Math.max(0, Math.floor(Number(trimmedBudget)));
      if (!Number.isNaN(nextBudget as number) && nextBudget !== (agent.token_budget ?? null)) {
        patch.token_budget = nextBudget;
      }
      if (maxRetries !== (agent.max_retries ?? 0)) patch.max_retries = maxRetries;
      if (
        patch.title !== undefined ||
        patch.task !== undefined ||
        patch.approval_policy !== undefined ||
        patch.token_budget !== undefined ||
        patch.max_retries !== undefined
      ) {
        await api.agents.update(agent.id, patch);
      }
      if (topicId !== agent.topic_id) {
        await api.agents.link(agent.id, { topic_id: topicId, chat_id: null });
      }
      await persistSchedule();
      // "Save & run" launches the (freshly saved) objective. `start` clears the
      // prior run's artifacts and replays the task, so it doubles as the "run it
      // now" action whether or not the instructions changed.
      if (run) {
        await api.agents.start(agent.id);
      }
      // Re-fetch so the returned agent reflects title/task/link, the embedded
      // schedule summary (selectin-loaded server-side), and the new run status.
      const updated = await api.agents.get(agent.id);
      onSaved(updated);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
      setSavingRun(false);
    }
  }

  // Apply the schedule edits as part of Save. Creating the first schedule, then
  // toggling its enabled flag, or updating the cadence/clear-context.
  async function persistSchedule(): Promise<void> {
    const recur = recurrenceToPayload(recurrence);
    if (scheduleOn) {
      const payload = { ...recur, clear_context: clearContext, enabled: true };
      if (hasSchedule) {
        await api.agents.updateSchedule(agent.id, payload);
      } else {
        await api.agents.createSchedule(agent.id, payload);
      }
    } else if (hasSchedule) {
      // Pause (keep the config) rather than delete it on toggle-off.
      await api.agents.updateSchedule(agent.id, { enabled: false });
    }
  }

  async function runScheduleNow(): Promise<void> {
    if (scheduleBusy) return;
    setScheduleBusy(true);
    setError(null);
    try {
      await api.agents.runScheduleNow(agent.id);
      const updated = await api.agents.get(agent.id);
      onSaved(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setScheduleBusy(false);
    }
  }

  async function archive(): Promise<void> {
    setArchiving(true);
    setError(null);
    try {
      await api.agents.archive(agent.id);
      onArchived();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setArchiving(false);
    }
  }

  async function remove(): Promise<void> {
    if (
      !(await confirmAction({
        message: `Delete agent "${agent.title}"? Its timeline and runtime session are removed. Any messages it posted into a topic stay, but lose the link back here.`,
        confirmLabel: "Delete agent",
        variant: "danger",
      }))
    )
      return;
    setDeleting(true);
    setError(null);
    try {
      await api.agents.remove(agent.id);
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setDeleting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-stretch justify-end z-50"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-[min(480px,100%)] h-full bg-bg border-l border-border flex flex-col">
        <header className="flex items-center justify-between px-4 h-12 border-b border-border">
          <h2 className="font-semibold truncate">Agent settings</h2>
          <button
            onClick={onClose}
            className="p-1.5 rounded hover:bg-surface"
            aria-label="Close"
            data-tooltip="Close"
          >
            <X size={18} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto">
          <div className="p-4 space-y-4">
            <section>
              <label className="block text-xs text-muted mb-1">Title</label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
              />
            </section>

            <section>
              <div className="flex items-center justify-between mb-1">
                <label className="block text-xs text-muted">Status</label>
                <AgentStatusBadge status={agent.status} />
              </div>
            </section>

            {/* Agents are shared, so knowing an edit here ripples into N
                pipelines matters before you make one. */}
            <section>
              <button
                type="button"
                onClick={() => setShowWorkflows((v) => !v)}
                disabled={workflows === null || workflows.length === 0}
                className="flex w-full items-center justify-between text-left disabled:cursor-default"
              >
                <span className="block text-xs text-muted">Used in workflows</span>
                <span
                  className={`flex items-center gap-1 rounded px-1.5 py-0.5 text-xs ${
                    workflows && workflows.length > 0
                      ? "bg-accent/15 text-accent"
                      : "text-muted"
                  }`}
                >
                  <WorkflowIcon size={12} />
                  {workflows === null ? "…" : workflows.length}
                </span>
              </button>
              {showWorkflows && workflows && workflows.length > 0 && (
                <ul className="mt-1.5 space-y-1">
                  {workflows.map((w) => (
                    <li key={w.id}>
                      <button
                        type="button"
                        onClick={() => onOpenWorkflow?.(w.id)}
                        className="flex w-full items-center gap-1.5 rounded border border-border bg-surface px-2 py-1 text-left text-xs transition hover:border-accent/50"
                      >
                        {w.icon ? <span>{w.icon}</span> : <WorkflowIcon size={12} />}
                        <span className="truncate">{w.name}</span>
                        <span className="ml-auto shrink-0 text-[10px] text-muted">{w.status}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section>
              <label className="block text-xs text-muted mb-1">Instructions (task)</label>
              <RefineTextarea
                value={task}
                onValueChange={setTask}
                refineKind="instructions"
                disabled={taskLocked || saving}
                rows={10}
                spellCheck={false}
                className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm font-mono leading-snug outline-none focus:border-accent disabled:opacity-60 resize-y"
              />
              <p className="text-[11px] text-muted mt-1">
                {taskLocked
                  ? "Stop the agent before editing its instructions."
                  : "Saving changed instructions re-establishes the session and replays them. The session id is kept, so scheduled /agent references keep working. To wipe prior context instead, use the agent's Clear action."}
              </p>
            </section>

            <section>
              <label className="block text-xs text-muted mb-1">Associated topic</label>
              <TopicPicker topics={topics} value={topicId} onChange={setTopicId} disabled={saving} collections={collections} />
              <p className="text-[11px] text-muted mt-1">
                The agent reads this topic's context and posts its prompt + answer back here when it
                finishes. Changing it re-injects the new topic context on the next turn.
              </p>
            </section>

            <section>
              <label className="flex items-center gap-1.5 text-xs text-muted mb-1">
                <ShieldCheck size={13} />
                Approval policy
              </label>
              <Select
                fullWidth
                size="sm"
                ariaLabel="Approval policy for this agent"
                disabled={saving}
                value={approvalPolicy}
                onChange={(v) => setApprovalPolicy(v as AgentApprovalPolicy | "")}
                options={[
                  {
                    value: "",
                    label: `Inherit global default${
                      globalPolicyLabel ? ` — ${globalPolicyLabel}` : ""
                    }`,
                  },
                  ...APPROVAL_POLICIES.map((p) => ({ value: p.value, label: p.label })),
                ]}
              />
              <p className="text-[11px] text-muted mt-1 leading-relaxed">
                {approvalPolicy
                  ? (APPROVAL_POLICIES.find((p) => p.value === approvalPolicy)?.hint ?? "")
                  : "Falls back to the global default set in Settings. Takes effect on the agent's next turn — no session rebuild."}
              </p>
            </section>

            <section className="pt-4 border-t border-border space-y-4">
              <div className="flex items-center gap-1.5 text-xs font-medium text-muted">
                <Coins size={13} />
                Governance
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-muted mb-1">Token budget</label>
                  <input
                    type="number"
                    min={0}
                    step={1000}
                    value={tokenBudget}
                    onChange={(e) => setTokenBudget(e.target.value)}
                    placeholder="Unlimited"
                    disabled={saving}
                    className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent disabled:opacity-60"
                  />
                </div>
                <div>
                  <label className="flex items-center gap-1 text-xs text-muted mb-1">
                    <RefreshCw size={11} />
                    Max retries
                  </label>
                  <input
                    type="number"
                    min={0}
                    max={10}
                    step={1}
                    value={maxRetries}
                    onChange={(e) => setMaxRetries(Math.max(0, Math.floor(Number(e.target.value) || 0)))}
                    disabled={saving}
                    className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent disabled:opacity-60"
                  />
                </div>
              </div>
              <p className="text-[11px] text-muted leading-relaxed">
                When the agent's cumulative tokens cross the budget, it parks itself in the inbox for
                your approval instead of spending more. Retries auto-recover a failed run with
                exponential backoff before it is marked failed for good.
              </p>
            </section>

            <section className="pt-4 border-t border-border space-y-4">
              <label className="flex items-start gap-2.5 cursor-pointer">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={scheduleOn}
                  onChange={(e) => setScheduleOn(e.target.checked)}
                />
                <span className="space-y-0.5">
                  <span className="flex items-center gap-1.5 text-sm font-medium">
                    <CalendarClock size={14} className="text-muted" />
                    Run on a schedule
                  </span>
                  <span className="block text-[11px] text-muted leading-relaxed">
                    Re-runs this agent's task automatically on a recurrence.
                  </span>
                </span>
              </label>

              {scheduleOn && (
                <div className="ml-1.5 space-y-4 border-l border-border pl-4">
                  <RecurrenceEditor value={recurrence} onChange={setRecurrence} />

                  <label className="flex items-start gap-2.5 cursor-pointer">
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={clearContext}
                      onChange={(e) => setClearContext(e.target.checked)}
                    />
                    <span className="space-y-0.5">
                      <span className="block text-sm">Clear context before each run</span>
                      <span className="block text-[11px] text-muted leading-relaxed">
                        Wipes the prior transcript (keeping the session id) and replays the task
                        from scratch. Off = re-runs as a follow-up in the existing conversation.
                      </span>
                    </span>
                  </label>

                  {agent.schedule && <AgentScheduleMeta schedule={agent.schedule} />}

                  {hasSchedule && (
                    <button
                      onClick={() => void runScheduleNow()}
                      disabled={scheduleBusy || saving}
                      className="flex items-center gap-1.5 rounded border border-border px-2.5 py-1 text-xs hover:bg-surface disabled:opacity-50"
                    >
                      <Play size={12} /> Run now
                    </button>
                  )}
                </div>
              )}
            </section>

            {error && <p className="text-xs text-red-500">{error}</p>}

            <section className="pt-2 border-t border-border space-y-3">
              <div>
                <a
                  href={api.transfer.exportAgentUrl(agent.id)}
                  download
                  className="flex items-center gap-2 text-sm text-muted hover:text-text"
                >
                  <Download size={14} />
                  Export as YAML
                </a>
                <p className="text-[11px] text-muted mt-1">
                  A portable copy of this agent's definition — prompt, persona, budgets and
                  cadence. No run history, and no webhook tokens.
                </p>
              </div>
              <div>
                <button
                  onClick={() => void archive()}
                  disabled={archiving}
                  className="flex items-center gap-2 text-sm text-muted hover:text-text disabled:opacity-50"
                >
                  <Archive size={14} />
                  {archiving ? "Archiving…" : "Archive agent"}
                </button>
                <p className="text-[11px] text-muted mt-1">
                  Hides the agent from the list but keeps its history. Restore it any time from the
                  archive (click your profile in the sidebar).
                </p>
              </div>
              <div>
                <button
                  onClick={() => void remove()}
                  disabled={deleting}
                  className="flex items-center gap-2 text-sm text-red-500 hover:text-red-400 disabled:opacity-50"
                >
                  <Trash2 size={14} />
                  {deleting ? "Deleting…" : "Delete agent"}
                </button>
                <p className="text-[11px] text-muted mt-1">
                  Removes the agent and its runtime session. This can't be undone.
                </p>
              </div>
            </section>
          </div>
        </div>

        <footer className="border-t border-border p-3 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface"
          >
            Cancel
          </button>
          <button
            onClick={() => void save()}
            disabled={!title.trim() || saving}
            className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface disabled:opacity-50"
          >
            {saving && !savingRun ? "Saving…" : "Save"}
          </button>
          <button
            onClick={() => void save({ run: true })}
            disabled={!title.trim() || saving || isActive}
            title={
              isActive
                ? "Agent is already active — stop it before running again"
                : "Save changes and run the objective now"
            }
            className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
          >
            <Play size={14} />
            {savingRun ? "Starting…" : "Save & run"}
          </button>
        </footer>
      </div>
    </div>
  );
}

function AgentScheduleMeta({
  schedule,
}: {
  schedule: NonNullable<AgentSession["schedule"]>;
}) {
  return (
    <div className="rounded border border-border bg-surface/50 px-3 py-2 text-[11px] text-muted space-y-1">
      <div>
        Status: <span className="text-text">{schedule.status}</span>
        {!schedule.enabled && " (paused)"}
      </div>
      {schedule.next_run_at && (
        <div>Next run: {new Date(schedule.next_run_at).toLocaleString()}</div>
      )}
      {schedule.last_run_at && (
        <div>Last run: {new Date(schedule.last_run_at).toLocaleString()}</div>
      )}
    </div>
  );
}
