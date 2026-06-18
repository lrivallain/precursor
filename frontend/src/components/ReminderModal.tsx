import { useState } from "react";
import { Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import type { Reminder, ReminderContainer } from "../lib/types";

interface Props {
  container: ReminderContainer;
  containerId: number;
  /** Existing reminder to edit, or null when creating a new one. */
  existing: Reminder | null;
  /** Optional note prefilled from the slash-command argument. */
  initialNote?: string;
  onClose: () => void;
  onSaved: () => void;
}

// <input type="datetime-local"> wants a local "YYYY-MM-DDTHH:mm" value (no tz).
function toLocalInputValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

function defaultWhen(): string {
  const d = new Date(Date.now() + 60 * 60 * 1000); // one hour from now
  d.setSeconds(0, 0);
  return toLocalInputValue(d);
}

export function ReminderModal({
  container,
  containerId,
  existing,
  initialNote,
  onClose,
  onSaved,
}: Props) {
  const [when, setWhen] = useState<string>(
    existing ? toLocalInputValue(new Date(existing.remind_at)) : defaultWhen(),
  );
  const [note, setNote] = useState<string>(existing?.note ?? initialNote ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(): Promise<void> {
    if (submitting) return;
    const at = new Date(when);
    if (Number.isNaN(at.getTime())) {
      setError("Pick a valid date and time.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await api.setReminder(container, containerId, {
        remind_at: at.toISOString(),
        note: note.trim() || null,
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  async function remove(): Promise<void> {
    if (!existing || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.clearReminder(container, containerId);
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
        className="w-[min(460px,100%)] bg-bg border border-border rounded-lg shadow-lg flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-4 h-12 border-b border-border">
          <h2 className="font-semibold">
            {existing ? "Edit reminder" : "Set a reminder"}
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
          <div>
            <label className="block text-xs text-muted mb-1">Remind me on</label>
            <input
              autoFocus
              type="datetime-local"
              value={when}
              onChange={(e) => setWhen(e.target.value)}
              className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
            />
          </div>

          <div>
            <label className="block text-xs text-muted mb-1">Note (optional)</label>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              placeholder="What should this bring back up?"
              className="w-full resize-none bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
            />
          </div>

          {error && <p className="text-xs text-red-500">{error}</p>}
        </div>

        <footer className="border-t border-border p-3 flex items-center gap-2">
          {existing && (
            <button
              onClick={() => void remove()}
              disabled={submitting}
              className="p-1.5 rounded border border-border text-red-500 hover:bg-surface disabled:opacity-50"
              aria-label="Cancel reminder"
              data-tooltip="Cancel reminder"
            >
              <Trash2 size={16} />
            </button>
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
              {submitting ? "Saving…" : existing ? "Save" : "Set reminder"}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
