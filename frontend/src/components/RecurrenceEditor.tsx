// Shared recurrence editor for scheduled topics, agents and workflows.
// Each rule supports two modes:
//   - interval: "every N minutes/hours/days"
//   - daily:    "at HH:MM" on the selected weekdays (in the browser timezone)
// A schedule can hold *several* rules at once — "every day at 07:00" plus
// "every weekday at 12:00" — and fires at whichever comes first; see
// {@link RecurrenceListEditor}. Embeds the weekday picker so a consumer drops
// in one component.

import { Plus, X } from "lucide-react";
import { ALL_DAYS_MASK, WeekdayPicker } from "./WeekdayPicker";
import { Select } from "./Select";
import type { RecurrenceRule } from "../lib/types";

export type RecurrenceMode = "interval" | "daily";
export type IntervalUnit = "minutes" | "hours" | "days";

// Mirrors the backend's MAX_RULES guard rail.
export const MAX_RECURRENCE_RULES = 20;

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

// Read a whole rule set back into editor values. Falls back to the flat
// primary-rule fields for a server that predates `rules`.
export function recurrenceListFromSchedule(s: {
  interval_seconds: number;
  run_at_minute: number | null;
  days_of_week: number;
  rules?: RecurrenceRule[] | null;
}): RecurrenceValue[] {
  const rules = s.rules?.length ? s.rules : [s];
  return rules.map(recurrenceFromSchedule);
}

// Translate the editor value into the API recurrence fields. The browser's
// timezone is captured so "07:00" is interpreted as the user's local time.
export function recurrenceToPayload(v: RecurrenceValue): RecurrenceRule {
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

// Translate a whole rule set. The first rule is also spread into the flat
// fields so a payload stays readable by (and valid for) the legacy shape.
export function recurrenceListToPayload(values: RecurrenceValue[]): RecurrenceRule & {
  rules: RecurrenceRule[];
} {
  const rules = (values.length ? values : [defaultRecurrence()]).map(recurrenceToPayload);
  return { ...rules[0], rules };
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
            <Select
              value={value.intervalUnit}
              onChange={(v) => set({ intervalUnit: v as IntervalUnit })}
              ariaLabel="Interval unit"
              fullWidth
              options={[
                { value: "minutes", label: "minutes" },
                { value: "hours", label: "hours" },
                { value: "days", label: "days" },
              ]}
            />
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

interface ListProps {
  value: RecurrenceValue[];
  onChange: (next: RecurrenceValue[]) => void;
}

/**
 * Editor for a schedule's whole rule set.
 *
 * One rule behaves exactly like the single {@link RecurrenceEditor} it wraps —
 * no extra chrome until a second rule exists. Adding one lets a schedule
 * combine cadences ("every day at 07:00" *and* "every weekday at 12:00"); the
 * item then runs at whichever comes first. The last rule can't be removed:
 * a schedule with nothing to fire on would be a paused schedule, which is what
 * the enable toggle is for.
 */
export function RecurrenceListEditor({ value, onChange }: ListProps) {
  const rules = value.length ? value : [defaultRecurrence()];
  const replace = (index: number, next: RecurrenceValue) =>
    onChange(rules.map((rule, i) => (i === index ? next : rule)));
  const remove = (index: number) => onChange(rules.filter((_, i) => i !== index));

  return (
    <div className="space-y-3">
      {rules.map((rule, index) => (
        <div
          // Rules are an ordered, unkeyed list the user edits in place, so the
          // index is the identity here.
          key={index}
          className={
            rules.length > 1 ? "rounded-lg border border-border bg-surface/40 p-3" : undefined
          }
        >
          {rules.length > 1 && (
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[11px] font-medium uppercase tracking-wide text-muted">
                Rule {index + 1}
              </span>
              <button
                type="button"
                onClick={() => remove(index)}
                aria-label={`Remove rule ${index + 1}`}
                data-tooltip="Remove this rule"
                className="rounded p-1 text-muted transition-colors hover:text-red-500"
              >
                <X size={13} />
              </button>
            </div>
          )}
          <RecurrenceEditor value={rule} onChange={(next) => replace(index, next)} />
        </div>
      ))}

      {rules.length < MAX_RECURRENCE_RULES && (
        <button
          type="button"
          onClick={() => onChange([...rules, defaultRecurrence()])}
          data-tooltip="Run on more than one cadence, e.g. every day at 07:00 and every weekday at noon"
          className="flex items-center gap-1.5 rounded border border-dashed border-border px-2.5 py-1.5 text-xs text-muted transition-colors hover:border-accent hover:text-text"
        >
          <Plus size={13} />
          Add another schedule
        </button>
      )}

      {rules.length > 1 && (
        <p className="text-[11px] text-muted">Runs at whichever rule comes first.</p>
      )}
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
