import { AlarmClock, Check, Pencil } from "lucide-react";
import type { Reminder } from "../lib/types";

export interface ReminderPanelControls {
  reminder: Reminder | null;
  busy: boolean;
  /** Open the reminder editor (create, edit or cancel). */
  onEdit: () => void;
  /** Acknowledge a fired reminder. */
  onDone: () => void;
}

const WHEN_FORMAT: Intl.DateTimeFormatOptions = {
  weekday: "short",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
};

const RELATIVE_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
];

export function relativeWhen(target: Date, now: number = Date.now()): string {
  const seconds = (target.getTime() - now) / 1000;
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, size] of RELATIVE_UNITS) {
    if (Math.abs(seconds) >= size) return rtf.format(Math.round(seconds / size), unit);
  }
  return seconds >= 0 ? "in less than a minute" : "just now";
}

export function formatReminderWhen(reminder: Reminder): string {
  return new Date(reminder.remind_at).toLocaleString(undefined, WHEN_FORMAT);
}

/** Tooltip for the collapsed rail's reminder indicator. */
export function reminderSummary(reminder: Reminder): string {
  const note = (reminder.note ?? "").trim();
  const head =
    reminder.status === "fired"
      ? `Reminder fired — ${formatReminderWhen(reminder)}`
      : `Reminder ${relativeWhen(new Date(reminder.remind_at))} — ${formatReminderWhen(reminder)}`;
  return note ? `${head}\n${note}` : head;
}

export function ReminderSection({ reminder, busy, onEdit, onDone }: ReminderPanelControls) {
  const fired = reminder?.status === "fired";
  const note = (reminder?.note ?? "").trim();
  return (
    <div data-testid="stats-reminder">
      <div className="text-xs uppercase tracking-wide text-muted mb-1">Reminder</div>
      {reminder ? (
        <div
          className={`rounded-md border p-2 space-y-1 ${
            fired ? "border-amber-500/50 bg-amber-500/10" : "border-border bg-surface/60"
          }`}
        >
          <div className="flex items-center gap-1.5">
            <AlarmClock
              size={14}
              className={`shrink-0 ${fired ? "text-amber-600 dark:text-amber-400" : "text-accent"}`}
            />
            <span className="font-medium tabular-nums">{formatReminderWhen(reminder)}</span>
          </div>
          <div className="text-xs text-muted">
            {fired ? "Fired, waiting to be marked done" : relativeWhen(new Date(reminder.remind_at))}
          </div>
          {note && (
            <p className="text-xs whitespace-pre-wrap break-words line-clamp-4" data-tooltip={note}>
              {note}
            </p>
          )}
          <div className="flex gap-1.5 pt-1">
            {fired && (
              <button
                type="button"
                onClick={onDone}
                disabled={busy}
                className="inline-flex flex-1 items-center justify-center gap-1 rounded-md bg-accent px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
                data-tooltip="Mark reminder handled (/done)"
              >
                <Check size={12} /> Done
              </button>
            )}
            <button
              type="button"
              onClick={onEdit}
              disabled={busy}
              className="inline-flex flex-1 items-center justify-center gap-1 rounded-md border border-border px-2 py-1 text-xs font-medium text-muted transition hover:bg-surface hover:text-text disabled:opacity-50"
              data-tooltip="Change the time or note, or cancel the reminder"
            >
              <Pencil size={12} /> Edit
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={onEdit}
          className="inline-flex w-full items-center justify-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs font-medium text-muted transition hover:bg-surface hover:text-text"
          data-tooltip="Resurface this conversation at a chosen time (/reminder)"
        >
          <AlarmClock size={12} /> Set reminder
        </button>
      )}
    </div>
  );
}
