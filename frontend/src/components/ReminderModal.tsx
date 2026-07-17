import { useMemo, useState } from "react";
import { Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import { Modal } from "./Modal";
import { RefineTextarea } from "./RefineTextarea";
import { Z_INDEX } from "../lib/constants";
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

// Combine the picked date + hour/minute into a local Date (interpreted in the
// user's own timezone, then sent to the backend as UTC on submit). Empty
// hour/minute fields count as 0.
function combine(dateStr: string, hh: string, mm: string): Date {
  return new Date(`${dateStr}T${pad(Number(hh) || 0)}:${pad(Number(mm) || 0)}`);
}

// Keep an "HH"/"MM" field within [0, max], tolerating mid-edit empty values.
function clampTime(raw: string, max: number): string {
  const digits = raw.replace(/\D/g, "").slice(0, 2);
  if (digits === "") return "";
  return String(Math.min(Number(digits), max));
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
  const [hh, setHh] = useState<string>(pad(initial.getHours()));
  const [mm, setMm] = useState<string>(pad(initial.getMinutes()));
  const [note, setNote] = useState<string>(existing?.note ?? initialNote ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isPast = useMemo(() => {
    const at = combine(date, hh, mm);
    return !Number.isNaN(at.getTime()) && at.getTime() <= Date.now();
  }, [date, hh, mm]);

  function applyPreset(d: Date): void {
    setDate(toDateValue(d));
    setHh(pad(d.getHours()));
    setMm(pad(d.getMinutes()));
  }

  async function submit(): Promise<void> {
    if (submitting) return;
    const at = combine(date, hh, mm);
    if (Number.isNaN(at.getTime())) {
      setError("Pick a valid date and time.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const saved = await api.reminders.set(container, containerId, {
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
      await api.reminders.clear(container, containerId);
      onSaved(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      onClose={onClose}
      zIndex={Z_INDEX.MODAL}
      panelClassName="w-[min(460px,100%)] bg-bg border border-border rounded-lg shadow-lg flex flex-col"
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
            <div className="w-36">
              <label className="block text-xs text-muted mb-1">Time</label>
              <div className="flex items-center gap-1">
                <input
                  type="number"
                  inputMode="numeric"
                  min={0}
                  max={23}
                  step={1}
                  value={hh}
                  aria-label="Hours"
                  onChange={(e) => setHh(clampTime(e.target.value, 23))}
                  onBlur={() => setHh((v) => pad(Number(v) || 0))}
                  className="w-14 bg-surface border border-border rounded px-2 py-1.5 text-sm text-center tabular-nums outline-none focus:border-accent"
                />
                <span className="text-muted">:</span>
                <input
                  type="number"
                  inputMode="numeric"
                  min={0}
                  max={59}
                  step={1}
                  value={mm}
                  aria-label="Minutes"
                  onChange={(e) => setMm(clampTime(e.target.value, 59))}
                  onBlur={() => setMm((v) => pad(Number(v) || 0))}
                  className="w-14 bg-surface border border-border rounded px-2 py-1.5 text-sm text-center tabular-nums outline-none focus:border-accent"
                />
              </div>
            </div>
          </div>

          {isPast && (
            <p className="text-xs text-muted">
              That time is in the past — this reminder will fire right away.
            </p>
          )}

          <div>
            <label className="block text-xs text-muted mb-1">Note (optional)</label>
            <RefineTextarea
              value={note}
              onValueChange={setNote}
              refineKind="note"
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
    </Modal>
  );
}
