import { useEffect, useState } from "react";
import { Archive, CalendarClock, Play, Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import type { AgentSession, Topic } from "../lib/types";
import { useConfirm } from "./ConfirmDialog";
import { RefineTextarea } from "./RefineTextarea";
import { AgentStatusBadge } from "./AgentStatusBadge";
import { TopicPicker } from "./AgentView";
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
  onDeleted: () => void;
}

// Mirrors ChatSettingsPanel (drawer + Title + footer Save + destructive
// archive/delete) so an agent session feels like the same surface as a topic or
// chat. Agent-specific bits: the editable task prompt (its "instructions") and
// the associated-topic picker. Renaming and linking reuse the same endpoints
// the header/timeline already drive. Editing the task re-establishes the SDK
// session server-side so the new instructions actually take effect.
export function AgentSettingsPanel({ agent, onClose, onSaved, onArchived, onDeleted }: Props) {
  const confirmAction = useConfirm();
  const [title, setTitle] = useState(agent.title);
  const [task, setTask] = useState(agent.task_prompt);
  const [topicId, setTopicId] = useState<number | null>(agent.topic_id);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [saving, setSaving] = useState(false);
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

  useEffect(() => {
    void api.topics.list()
      .then(setTopics)
      .catch(() => setTopics([]));
  }, []);

  async function save(): Promise<void> {
    const trimmedTitle = title.trim();
    const trimmedTask = task.trim();
    if (!trimmedTitle || saving) return;
    setSaving(true);
    setError(null);
    try {
      const patch: { title?: string; task?: string } = {};
      if (trimmedTitle !== agent.title) patch.title = trimmedTitle;
      if (trimmedTask && trimmedTask !== agent.task_prompt) patch.task = trimmedTask;
      if (patch.title !== undefined || patch.task !== undefined) {
        await api.agents.update(agent.id, patch);
      }
      if (topicId !== agent.topic_id) {
        await api.agents.link(agent.id, { topic_id: topicId, chat_id: null });
      }
      await persistSchedule();
      // Re-fetch so the returned agent reflects title/task/link *and* the
      // embedded schedule summary (selectin-loaded server-side).
      const updated = await api.agents.get(agent.id);
      onSaved(updated);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
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
              <TopicPicker topics={topics} value={topicId} onChange={setTopicId} disabled={saving} />
              <p className="text-[11px] text-muted mt-1">
                The agent reads this topic's context and posts its prompt + answer back here when it
                finishes. Changing it re-injects the new topic context on the next turn.
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
            className="px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
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
