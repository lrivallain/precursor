import { useEffect, useState } from "react";
import { Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import type { Schedule, ScheduleSummary } from "../lib/types";
import { useConfirm } from "./ConfirmDialog";
import {
  defaultRecurrence,
  recurrenceFromSchedule,
  recurrenceToPayload,
  RecurrenceEditor,
  type RecurrenceValue,
} from "./RecurrenceEditor";

interface Props {
  // When editing, the existing schedule; when creating, null.
  schedule: Schedule | null;
  onClose: () => void;
  onSaved: () => void;
}

export function ScheduleModal({ schedule, onClose, onSaved }: Props) {
  const confirmAction = useConfirm();
  const editing = schedule !== null;

  const [title, setTitle] = useState(""); // only used on create
  const [prompt, setPrompt] = useState(schedule?.prompt ?? "");
  const [recurrence, setRecurrence] = useState<RecurrenceValue>(
    schedule ? recurrenceFromSchedule(schedule) : defaultRecurrence(),
  );
  const [enabled, setEnabled] = useState<boolean>(schedule?.enabled ?? true);
  const [clearContext, setClearContext] = useState<boolean>(
    schedule?.clear_context ?? false,
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Keep local title in sync only on create (editing renames via the topic).
  useEffect(() => {
    if (!editing) setTitle("");
  }, [editing]);

  async function submit(): Promise<void> {
    if (submitting) return;
    if (!editing && !title.trim()) {
      setError("Title is required");
      return;
    }
    if (!prompt.trim()) {
      setError("Prompt is required");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const recur = recurrenceToPayload(recurrence);
      if (editing && schedule) {
        await api.updateSchedule(schedule.topic_id, {
          prompt: prompt.trim(),
          ...recur,
          clear_context: clearContext,
          enabled,
        });
      } else {
        await api.createSchedule({
          title: title.trim(),
          prompt: prompt.trim(),
          ...recur,
          clear_context: clearContext,
          enabled,
        });
      }
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  async function runNow(): Promise<void> {
    if (!schedule || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.runScheduleNow(schedule.topic_id);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  async function remove(): Promise<void> {
    if (!schedule || submitting) return;
    if (
      !(await confirmAction({
        message: "Delete this scheduled topic and its history?",
        confirmLabel: "Delete topic",
        variant: "danger",
      }))
    )
      return;
    setSubmitting(true);
    setError(null);
    try {
      await api.deleteSchedule(schedule.topic_id);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="w-[min(520px,100%)] bg-bg border border-border rounded-lg shadow-lg flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-4 h-12 border-b border-border">
          <h2 className="font-semibold">
            {editing ? "Edit scheduled topic" : "New scheduled topic"}
          </h2>
          <button
            onClick={onClose}
            className="p-1.5 rounded hover:bg-surface"
            aria-label="Close"
            data-tooltip="Close"
          >
            <X size={18} />
          </button>
        </header>

        <div className="p-4 space-y-4">
          {!editing && (
            <div>
              <label className="block text-xs text-muted mb-1">Title</label>
              <input
                autoFocus
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. Daily issue digest"
                className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
              />
            </div>
          )}

          <div>
            <label className="block text-xs text-muted mb-1">
              Prompt to run each time
            </label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              placeholder="What should the assistant do on every run?"
              className="w-full resize-none bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
            />
          </div>

          <RecurrenceEditor value={recurrence} onChange={setRecurrence} />

          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            <span>Enabled</span>
          </label>

          <label className="flex items-start gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={clearContext}
              onChange={(e) => setClearContext(e.target.checked)}
            />
            <span>
              Clear context before each run
              <span className="block text-[11px] text-muted">
                Wipes the topic's prior messages so every run starts fresh.
              </span>
            </span>
          </label>

          {editing && schedule && (
            <ScheduleMeta schedule={schedule} />
          )}

          {error && <p className="text-xs text-red-500">{error}</p>}
        </div>

        <footer className="border-t border-border p-3 flex items-center gap-2">
          {editing && (
            <>
              <button
                onClick={() => void remove()}
                disabled={submitting}
                className="p-1.5 rounded border border-border text-red-500 hover:bg-surface disabled:opacity-50"
                aria-label="Delete scheduled topic"
                data-tooltip="Delete scheduled topic"
              >
                <Trash2 size={16} />
              </button>
              <button
                onClick={() => void runNow()}
                disabled={submitting}
                className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface disabled:opacity-50"
              >
                Run now
              </button>
            </>
          )}
          <div className="ml-auto flex gap-2">
            <button
              onClick={onClose}
              className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface"
            >
              Cancel
            </button>
            <button
              onClick={() => void submit()}
              disabled={submitting}
              className="px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
            >
              {submitting ? "Saving…" : editing ? "Save" : "Create"}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

function ScheduleMeta({ schedule }: { schedule: ScheduleSummary & { last_error?: string | null } }) {
  return (
    <div className="rounded border border-border bg-surface/50 px-3 py-2 text-[11px] text-muted space-y-1">
      <div>
        Status: <span className="text-text">{schedule.status}</span>
      </div>
      {schedule.next_run_at && (
        <div>Next run: {new Date(schedule.next_run_at).toLocaleString()}</div>
      )}
      {schedule.last_run_at && (
        <div>Last run: {new Date(schedule.last_run_at).toLocaleString()}</div>
      )}
      {schedule.last_error && (
        <div className="text-red-500">Last error: {schedule.last_error}</div>
      )}
    </div>
  );
}
