// Auto-marking a conversation read on an *incoming* reply should only happen in
// the tab the user is actually looking at. A tab merely left open on a
// conversation in the background must not clear the unread for everyone (read
// state is shared server-side) — otherwise a reply that arrives while you're in
// another tab/app never shows as unread. Explicit actions (clicking a
// conversation open) mark read regardless; this gate is only for event-driven
// auto-marks. Mirrors the standard "unread accrues while the window isn't
// focused" behaviour (and how maybeNotify already keys off focus).
export function windowFocused(): boolean {
  return typeof document !== "undefined" && document.hasFocus();
}
