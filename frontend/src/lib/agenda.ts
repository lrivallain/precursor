import type { AgendaEvent } from "./types";

/**
 * Shared helpers for the M365 agenda lists used both to *start* a Live session
 * (``LiveStartHero``) and to *attach* a meeting to one (``ContextSection``).
 * They keep the two surfaces consistent: same "when" formatting, the same
 * past / current-or-future split, and the same ordering.
 */

function startMs(ev: AgendaEvent): number {
  return ev.start ? new Date(ev.start).getTime() : Number.NaN;
}

/** True while the meeting is happening right now (has a start and end around now). */
export function isOngoingMeeting(ev: AgendaEvent, now: number = Date.now()): boolean {
  const s = startMs(ev);
  const e = ev.end ? new Date(ev.end).getTime() : Number.NaN;
  if (Number.isNaN(s) || Number.isNaN(e)) return false;
  return s <= now && now < e;
}

/**
 * True once the meeting has finished — by its end time, or (lacking one) its
 * start time. Undated entries are treated as current/future so they stay
 * actionable rather than being buried under "past".
 */
export function isPastMeeting(ev: AgendaEvent, now: number = Date.now()): boolean {
  const e = ev.end ? new Date(ev.end).getTime() : Number.NaN;
  if (!Number.isNaN(e)) return e < now;
  const s = startMs(ev);
  if (!Number.isNaN(s)) return s < now;
  return false;
}

export interface PartitionedMeetings {
  /** Finished meetings, oldest → newest (closest to now sits last). */
  past: AgendaEvent[];
  /** Current + upcoming meetings, oldest → newest. */
  upcoming: AgendaEvent[];
}

/** Split an agenda into past vs current/future, each sorted by start time. */
export function partitionMeetings(
  events: AgendaEvent[],
  now: number = Date.now(),
): PartitionedMeetings {
  const past: AgendaEvent[] = [];
  const upcoming: AgendaEvent[] = [];
  for (const ev of events) (isPastMeeting(ev, now) ? past : upcoming).push(ev);
  const byStart = (a: AgendaEvent, b: AgendaEvent): number => {
    const sa = startMs(a);
    const sb = startMs(b);
    if (Number.isNaN(sa)) return Number.isNaN(sb) ? 0 : 1;
    if (Number.isNaN(sb)) return -1;
    return sa - sb;
  };
  past.sort(byStart);
  upcoming.sort(byStart);
  return { past, upcoming };
}

/**
 * Compact "when" label for an agenda entry: just the time for today's meetings,
 * or a short weekday+date prefix otherwise.
 */
export function formatMeetingWhen(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const today = new Date();
  const sameDay =
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate();
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  if (sameDay) return time;
  const day = d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  return `${day} ${time}`;
}
