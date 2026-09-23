import { useEffect, useMemo, useState } from "react";
import { Loader2, ScrollText, X } from "lucide-react";
import type { MeetingTranscriptPart } from "../lib/types";
import { Modal } from "./Modal";

interface Props {
  parts: MeetingTranscriptPart[];
  /** The linked meeting's scheduled window, used to pre-select the right parts. */
  meetingStart: string | null;
  meetingEnd: string | null;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (transcriptIds: string[]) => void;
}

// Teams often starts transcription a little before the slot and stops after it,
// so the window we match parts against is padded on both sides.
const WINDOW_PADDING_MS = 30 * 60 * 1000;

function stamp(value: string | null | undefined): Date | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "22 Sep 2026, 15:30 – 17:00" (a same-day end collapses to the time only). */
function formatRange(part: MeetingTranscriptPart): string {
  const start = stamp(part.created_at);
  const end = stamp(part.ended_at);
  if (!start) return "Unknown time";
  const startLabel = start.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
  if (!end) return startLabel;
  const endLabel = end.toLocaleString(
    undefined,
    start.toDateString() === end.toDateString()
      ? { timeStyle: "short" }
      : { dateStyle: "medium", timeStyle: "short" },
  );
  return `${startLabel} – ${endLabel}`;
}

/** "1 h 27 min" — only when both ends are known. */
function formatDuration(part: MeetingTranscriptPart): string | null {
  const start = stamp(part.created_at);
  const end = stamp(part.ended_at);
  if (!start || !end) return null;
  const minutes = Math.round((end.getTime() - start.getTime()) / 60000);
  if (minutes <= 0) return null;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h ? `${h} h ${m} min` : `${m} min`;
}

function overlapsMeeting(
  part: MeetingTranscriptPart,
  windowStart: Date | null,
  windowEnd: Date | null,
): boolean {
  if (!windowStart || !windowEnd) return false;
  const start = stamp(part.created_at);
  if (!start) return false;
  const end = stamp(part.ended_at) ?? start;
  return (
    end.getTime() >= windowStart.getTime() - WINDOW_PADDING_MS &&
    start.getTime() <= windowEnd.getTime() + WINDOW_PADDING_MS
  );
}

/**
 * Asks which Teams transcription session(s) to summarise when the meeting has
 * more than one. Teams starts a new transcript each time transcription is
 * stopped and restarted, so a meeting that got cut shows up as "Partie 1",
 * "Partie 2", … — hence multi-select.
 *
 * A *recurring* meeting reuses its join URL, so the list can also span other
 * occurrences: the default selection is therefore the parts falling inside the
 * linked meeting's scheduled window, and only the most recent one when that
 * window is unknown.
 */
export function TranscriptPartPicker({
  parts,
  meetingStart,
  meetingEnd,
  busy,
  onCancel,
  onConfirm,
}: Props) {
  const rows = useMemo(() => {
    const windowStart = stamp(meetingStart);
    const windowEnd = stamp(meetingEnd);
    return parts.map((part, index) => ({
      part,
      label: `Part ${index + 1}`,
      range: formatRange(part),
      duration: formatDuration(part),
      matches: overlapsMeeting(part, windowStart, windowEnd),
    }));
  }, [parts, meetingStart, meetingEnd]);

  const defaults = useMemo(() => {
    const matching = rows.filter((r) => r.matches).map((r) => r.part.id);
    if (matching.length > 0) return matching;
    return parts.length > 0 ? [parts[parts.length - 1].id] : [];
  }, [rows, parts]);

  const [selected, setSelected] = useState<string[]>(defaults);
  useEffect(() => setSelected(defaults), [defaults]);

  function toggle(id: string): void {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  return (
    <Modal
      onClose={busy ? undefined : onCancel}
      closeOnEscape={!busy}
      padded
      labelledBy="transcript-picker-title"
      panelClassName="flex max-h-[80vh] w-[min(480px,100%)] flex-col overflow-hidden rounded-lg border border-border bg-bg shadow-xl"
    >
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <ScrollText size={15} className="text-muted" />
        <h2 id="transcript-picker-title" className="text-sm font-medium">
          Which transcription session?
        </h2>
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          aria-label="Cancel"
          className="ml-auto text-muted hover:text-text disabled:opacity-50"
        >
          <X size={15} />
        </button>
      </div>

      <p className="border-b border-border px-4 py-2 text-[12px] text-muted">
        Teams recorded {parts.length} transcription sessions for this meeting — it
        starts a new one every time transcription is stopped and restarted. Pick
        the one you want, or several if the meeting was cut: they&apos;ll be
        summarised together, in order.
      </p>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {rows.map(({ part, label, range, duration, matches }) => (
          <label
            key={part.id}
            className={`flex cursor-pointer items-start gap-2.5 rounded px-2 py-2 hover:bg-surface ${
              selected.includes(part.id) ? "bg-surface" : ""
            }`}
          >
            <input
              type="checkbox"
              checked={selected.includes(part.id)}
              onChange={() => toggle(part.id)}
              disabled={busy}
              className="mt-1 accent-accent"
            />
            <span className="min-w-0">
              <span className="flex items-center gap-1.5 text-[13px] font-medium">
                {label}
                {matches && (
                  <span className="rounded-full border border-border px-1.5 py-px text-[10px] font-normal text-muted">
                    matches this meeting
                  </span>
                )}
              </span>
              <span className="block text-[12px] text-muted">
                {range}
                {duration ? ` · ${duration}` : ""}
              </span>
            </span>
          </label>
        ))}
      </div>

      <div className="flex items-center gap-2 border-t border-border px-4 py-3">
        <span className="text-[11px] text-muted">
          {selected.length} of {parts.length} selected
        </span>
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="ml-auto rounded border border-border px-3 py-1.5 text-[13px] hover:bg-surface disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => onConfirm(selected)}
          disabled={busy || selected.length === 0}
          className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-[13px] text-white disabled:opacity-50"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : null}
          {busy ? "Summarising…" : "Summarise"}
        </button>
      </div>
    </Modal>
  );
}
