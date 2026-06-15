// Weekday selector for scheduled topics. Days are stored as a 7-bit mask
// matching Python's datetime.weekday(): bit 0 = Monday … bit 6 = Sunday.

export const ALL_DAYS_MASK = 127;
export const WEEKDAYS_MASK = 0b0011111; // Mon–Fri

// Index 0..6 = Monday..Sunday; `label` is the compact toggle text.
const DAYS: { index: number; label: string; full: string }[] = [
  { index: 0, label: "M", full: "Monday" },
  { index: 1, label: "T", full: "Tuesday" },
  { index: 2, label: "W", full: "Wednesday" },
  { index: 3, label: "T", full: "Thursday" },
  { index: 4, label: "F", full: "Friday" },
  { index: 5, label: "S", full: "Saturday" },
  { index: 6, label: "S", full: "Sunday" },
];

interface Props {
  value: number;
  onChange: (mask: number) => void;
}

export function WeekdayPicker({ value, onChange }: Props) {
  function toggle(index: number): void {
    const next = value ^ (1 << index);
    // Never allow an empty selection — a schedule with no days never runs.
    if (next === 0) return;
    onChange(next);
  }

  return (
    <div className="space-y-1.5">
      <div className="flex gap-1">
        {DAYS.map((d) => {
          const on = (value & (1 << d.index)) !== 0;
          return (
            <button
              key={d.index}
              type="button"
              onClick={() => toggle(d.index)}
              aria-pressed={on}
              aria-label={d.full}
              data-tooltip={d.full}
              className={`w-7 h-7 rounded text-xs font-medium border transition-colors ${
                on
                  ? "bg-accent text-white border-accent"
                  : "bg-surface text-muted border-border hover:text-text"
              }`}
            >
              {d.label}
            </button>
          );
        })}
      </div>
      <div className="flex gap-2 text-[11px]">
        <button
          type="button"
          onClick={() => onChange(WEEKDAYS_MASK)}
          className="text-accent hover:underline"
        >
          Weekdays
        </button>
        <span className="text-border">·</span>
        <button
          type="button"
          onClick={() => onChange(ALL_DAYS_MASK)}
          className="text-accent hover:underline"
        >
          Every day
        </button>
      </div>
    </div>
  );
}
