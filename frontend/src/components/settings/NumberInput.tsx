import { useEffect, useState } from "react";

// A number input that lets the user type freely (including clearing the field
// and entering large values like 600000) and only clamps to [min, max] on blur,
// so an in-progress value isn't rewritten on every keystroke.
export function NumberInput({
  value,
  min,
  max,
  onCommit,
}: {
  value: number;
  min: number;
  max: number;
  onCommit: (n: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  // Re-sync when the committed value changes (e.g. after clamping or reload).
  useEffect(() => {
    setDraft(String(value));
  }, [value]);
  return (
    <input
      type="number"
      min={min}
      max={max}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        const n = Number(draft);
        const clamped = Number.isFinite(n)
          ? Math.max(min, Math.min(max, n))
          : min;
        onCommit(clamped);
        setDraft(String(clamped));
      }}
      className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
    />
  );
}
