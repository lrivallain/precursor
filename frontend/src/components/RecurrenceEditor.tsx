// Shared recurrence editor for scheduled topics. Supports two modes:
//   - interval: "every N minutes/hours/days"
//   - daily:    "at HH:MM" on the selected weekdays (in the browser timezone)
// Embeds the weekday picker so a consumer drops in one component.

import { ALL_DAYS_MASK, WeekdayPicker } from "./WeekdayPicker";

export type RecurrenceMode = "interval" | "daily";
export type IntervalUnit = "minutes" | "hours" | "days";

const UNIT_SECONDS: Record<IntervalUnit, number> = {
  minutes: 60,
  hours: 3600,
  days: 86400,
};

export interface RecurrenceValue {
  mode: RecurrenceMode;
  // Interval mode:
  intervalValue: number;
  intervalUnit: IntervalUnit;
  // Daily mode (minutes since local midnight, 0..1439):
  runAtMinute: number;
  // Shared:
  daysMask: number;
}

// Pick the largest unit that represents `seconds` as a whole number, so an
// interval saved as 7200s reads back as "2 hours".
function splitInterval(seconds: number): { value: number; unit: IntervalUnit } {
  for (const unit of ["days", "hours", "minutes"] as IntervalUnit[]) {
    const div = UNIT_SECONDS[unit];
    if (seconds % div === 0 && seconds >= div) {
      return { value: seconds / div, unit };
    }
  }
  return { value: Math.max(1, Math.round(seconds / 60)), unit: "minutes" };
}

export function defaultRecurrence(): RecurrenceValue {
  return {
    mode: "interval",
    intervalValue: 1,
    intervalUnit: "hours",
    runAtMinute: 7 * 60, // 07:00
    daysMask: ALL_DAYS_MASK,
  };
}

export function recurrenceFromSchedule(s: {
  interval_seconds: number;
  run_at_minute: number | null;
  days_of_week: number;
}): RecurrenceValue {
  const { value, unit } = splitInterval(s.interval_seconds);
  const daily = s.run_at_minute !== null;
  return {
    mode: daily ? "daily" : "interval",
    intervalValue: value,
    intervalUnit: unit,
    runAtMinute: daily ? s.run_at_minute! : 7 * 60,
    daysMask: s.days_of_week,
  };
}

// Translate the editor value into the API recurrence fields. The browser's
// timezone is captured so "07:00" is interpreted as the user's local time.
export function recurrenceToPayload(v: RecurrenceValue): {
  interval_seconds: number;
  days_of_week: number;
  run_at_minute: number | null;
  timezone: string;
} {
  const tz =
    typeof Intl !== "undefined"
      ? Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
      : "UTC";
  return {
    interval_seconds: Math.max(60, Math.round(v.intervalValue) * UNIT_SECONDS[v.intervalUnit]),
    days_of_week: v.daysMask,
    run_at_minute: v.mode === "daily" ? v.runAtMinute : null,
    timezone: tz,
  };
}

function minuteToHHMM(minute: number): string {
  const h = Math.floor(minute / 60);
  const m = minute % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

function hhmmToMinute(value: string): number {
  const [h, m] = value.split(":").map((n) => Number.parseInt(n, 10));
  if (Number.isNaN(h) || Number.isNaN(m)) return 0;
  return Math.max(0, Math.min(h * 60 + m, 24 * 60 - 1));
}

interface Props {
  value: RecurrenceValue;
  onChange: (next: RecurrenceValue) => void;
}

export function RecurrenceEditor({ value, onChange }: Props) {
  const set = (patch: Partial<RecurrenceValue>) => onChange({ ...value, ...patch });

  return (
    <div className="space-y-3">
      <div>
        <label className="block text-xs text-muted mb-1">Recurrence</label>
        <div className="flex gap-1">
          <ModeButton
            active={value.mode === "interval"}
            onClick={() => set({ mode: "interval" })}
          >
            Every…
          </ModeButton>
          <ModeButton
            active={value.mode === "daily"}
            onClick={() => set({ mode: "daily" })}
          >
            At a time
          </ModeButton>
        </div>
      </div>

      {value.mode === "interval" ? (
        <div>
          <label className="block text-xs text-muted mb-1">Repeat every</label>
          <div className="grid grid-cols-[120px_1fr] gap-2">
            <input
              type="number"
              min={1}
              value={value.intervalValue}
              onChange={(e) => set({ intervalValue: Number(e.target.value) })}
              className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
            />
            <select
              value={value.intervalUnit}
              onChange={(e) => set({ intervalUnit: e.target.value as IntervalUnit })}
              className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
            >
              <option value="minutes">minutes</option>
              <option value="hours">hours</option>
              <option value="days">days</option>
            </select>
          </div>
          <p className="mt-1 text-[11px] text-muted">Minimum interval is 1 minute.</p>
        </div>
      ) : (
        <div>
          <label className="block text-xs text-muted mb-1">Run at</label>
          <input
            type="time"
            value={minuteToHHMM(value.runAtMinute)}
            onChange={(e) => set({ runAtMinute: hhmmToMinute(e.target.value) })}
            className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
          />
          <p className="mt-1 text-[11px] text-muted">
            Runs once on each selected day at this time (your local timezone).
          </p>
        </div>
      )}

      <div>
        <label className="block text-xs text-muted mb-1">
          {value.mode === "daily" ? "On days" : "Run on days"}
        </label>
        <WeekdayPicker value={value.daysMask} onChange={(daysMask) => set({ daysMask })} />
      </div>
    </div>
  );
}

function ModeButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`px-3 py-1.5 rounded text-xs font-medium border transition-colors ${
        active
          ? "bg-accent text-white border-accent"
          : "bg-surface text-muted border-border hover:text-text"
      }`}
    >
      {children}
    </button>
  );
}
