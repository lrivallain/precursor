import type { IssueLabel } from "../lib/types";

interface BadgeProps {
  state: string;
}

export function IssueStateBadge({ state }: BadgeProps) {
  const open = state === "open";
  const cls = open
    ? "border-green-500/40 text-green-500 bg-green-500/5"
    : "border-purple-500/40 text-purple-500 bg-purple-500/5";
  return (
    <span
      className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border ${cls}`}
      data-tooltip={`Issue is ${state}`}
    >
      {state}
    </span>
  );
}

interface LabelProps {
  label: IssueLabel;
}

export function IssueLabelChip({ label }: LabelProps) {
  const bg = `#${label.color}`;
  const fg = readableTextColor(label.color);
  return (
    <span
      className="text-[10px] px-1.5 py-0.5 rounded font-medium leading-tight"
      style={{ backgroundColor: bg, color: fg }}
      title={label.name}
    >
      {label.name}
    </span>
  );
}

function readableTextColor(hex: string): string {
  const m = hex.match(/^([0-9a-f]{6})$/i);
  if (!m) return "#000";
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  // Perceived luminance (Rec. 709).
  const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  return lum > 0.6 ? "#1a1a1a" : "#fff";
}
