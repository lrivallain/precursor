import { ShieldAlert } from "lucide-react";

/**
 * Sidebar marker for a conversation parked on tool approvals. Amber with a
 * shield so it never reads as an unread count (blue).
 */
export function ApprovalBadge({ count }: { count: number }) {
  if (count <= 0) return null;
  const label = `${count} tool ${count === 1 ? "call" : "calls"} awaiting approval`;
  return (
    <span
      className="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-amber-500 px-1 text-[10px] font-semibold leading-4 text-white tabular-nums"
      aria-label={label}
      data-tooltip={label}
    >
      <ShieldAlert size={10} aria-hidden />
      {count > 9 ? "9+" : count}
    </span>
  );
}
