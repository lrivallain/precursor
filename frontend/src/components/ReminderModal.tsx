import { useMemo, useState } from "react";
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
  onSaved: (reminder: Reminder | null) => void;
}

const pad = (n: number) => String(n).padStart(2, "0");

// <input type="date"> wants a local "YYYY-MM-DD" value.
function toDateValue(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// The time <select> options are local "HH:mm" strings.
function toTimeValue(d: Date): string {
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// Combine the picked date + time into a local Date (interpreted in the user's
// own timezone, then sent to the backend as UTC on submit).
function combine(dateStr: string, timeStr: string): Date {
  return new Date(`${dateStr}T${timeStr}`);
}

function defaultWhen(): Date {
  const d = new Date(Date.now() + 60 * 60 * 1000); // one hour from now
  d.setSeconds(0, 0);
  return d;
}

// Quick-pick presets. Each returns the target Date when clicked.
const PRESETS: { label: string; make: () => Date }[] = [
  {
    label: "In 1 hour",
    make: () => {
      const d = new Date(Date.now() + 60 * 60 * 1000);
      d.setSeconds(0, 0);
      return d;
    },
  },
  {
    label: "In 3 hours",
    make: () => {
      const d = new Date(Date.now() + 3 * 60 * 60 * 1000);
      d.setSeconds(0, 0);
      return d;
    },
  },
  {
    label: "This evening",
    make: () => {
      const d = new Date();
      d.setHours(18, 0, 0, 0);
      // Already past 18:00 → roll to tomorrow evening.
      if (d.getTime() <= Date.now()) d.setDate(d.getDate() + 1);
      return d;
    },
  },
  {
    label: "Tomorrow 9 AM",
    make: () => {
      const d = new Date();
      d.setDate(d.getDate() + 1);
      d.setHours(9, 0, 0, 0);
      return d;
    },
  },
  {
    label: "Next week",
    make: () => {
      const d = new Date();
      d.setDate(d.getDate() + 7);
      d.setHours(9, 0, 0, 0);
      return d;
    },
  },
];

// 15-minute time grid, labelled in the user's locale (value stays "HH:mm").
function buildTimeOptions(extra: string): { value: string; label: string }[] {
  const fmt = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
  const opts: { value: string; label: string }[] = [];
  const seen = new Set<string>();
  const add = (value: string) => {
    if (seen.has(value)) return;
    seen.add(value);
    const [h, m] = value.split(":").map(Number);
    opts.push({ value, label: fmt.format(new Date(2000, 0, 1, h, m)) });
  };
  // Keep an off-grid value (preset like "in 1 hour" or an edited reminder)
  // selectable by prepending it.
  if (extra && Number(extra.slice(3)) % 15 !== 0) {
    add(extra);
  }
  for (let h = 0; h < 24; h += 1) {
    for (let m = 0; m < 60; m += 15) {
      add(`${pad(h)}:${pad(m)}`);
    }
  }
  return opts;
}

export function ReminderModal({
  container,
  containerId,
  existing,
  initialNote,
  onClose,
  onSaved,
}: Props) {
  const initial = existing ? new Date(existing.remind_at) : defaultWhen();
  const [date, setDate] = useState<string>(toDateValue(initial));
  const [time, setTime] = useState<string>(toTimeValue(initial));
  const [note, setNote] = useState<string>(existing?.note ?? initialNote ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const timeOptions = useMemo(() => buildTimeOptions(time), [time]);
  const isPast = useMemo(() => {
    const at = combine(date, time);
    return !Number.isNaN(at.getTime()) && at.getTime() <= Date.now();
  }, [date, time]);

  function applyPreset(d: Date): void {
    setDate(toDateValue(d));
    setTime(toTimeValue(d));
  }

  async function submit(): Promise<void> {
    if (submitting) return;
    const at = combine(date, time);
    if (Number.isNaN(at.getTime())) {
      setError("Pick a valid date and time.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const saved = await api.setReminder(container, containerId, {
        remind_at: at.toISOString(),
        note: note.trim() || null,
      });
      onSaved(saved);
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
      onSaved(null);
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
            <label className="block text-xs text-muted mb-1.5">Quick pick</label>
            <div className="flex flex-wrap gap-1.5">
              {PRESETS.map((p) => (
                <button
                  key={p.label}
                  type="button"
                  onClick={() => applyPreset(p.make())}
                  className="px-2.5 py-1 rounded-full border border-border text-xs hover:bg-surface hover:border-accent"
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex gap-2">
            <div className="flex-1">
              <label className="block text-xs text-muted mb-1">Date</label>
              <input
                type="date"
                value={date}
                min={toDateValue(new Date())}
                onChange={(e) => setDate(e.target.value)}
                className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
              />
            </div>
            <div className="w-32">
              <label className="block text-xs text-muted mb-1">Time</label>
              <select
                value={time}
                onChange={(e) => setTime(e.target.value)}
                className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
              >
                {timeOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {isPast && (
            <p className="text-xs text-muted">
              That time is in the past — this reminder will fire right away.
            </p>
          )}

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
