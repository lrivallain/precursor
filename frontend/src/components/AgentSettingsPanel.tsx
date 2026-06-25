import { useEffect, useState } from "react";
import { Archive, Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import type { AgentSession, Topic } from "../lib/types";
import { useConfirm } from "./ConfirmDialog";
import { AgentStatusBadge } from "./AgentStatusBadge";
import { TopicPicker } from "./AgentView";

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

  // The task can't be replayed while a turn is in flight; the server rejects it.
  const taskLocked = ["pending", "running", "needs_approval"].includes(agent.status);

  useEffect(() => {
    void api
      .listTopics()
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
      let updated = agent;
      const patch: { title?: string; task?: string } = {};
      if (trimmedTitle !== agent.title) patch.title = trimmedTitle;
      if (trimmedTask && trimmedTask !== agent.task_prompt) patch.task = trimmedTask;
      if (patch.title !== undefined || patch.task !== undefined) {
        updated = await api.updateAgent(agent.id, patch);
      }
      if (topicId !== agent.topic_id) {
        updated = await api.linkAgent(agent.id, { topic_id: topicId, chat_id: null });
      }
      onSaved(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function archive(): Promise<void> {
    setArchiving(true);
    setError(null);
    try {
      await api.archiveAgent(agent.id);
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
      await api.deleteAgent(agent.id);
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
              <textarea
                value={task}
                onChange={(e) => setTask(e.target.value)}
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
