import { useCallback, useMemo, useSyncExternalStore } from "react";

import type { SidebarMode } from "../components/Sidebar";

const STORAGE_KEY = "precursor:sidebar:sectionOrder";

// Merge a persisted order with the canonical set of sections: keep the stored
// ordering for sections that still exist, drop any that no longer do, and
// append newly-shipped sections at the end so they surface without wiping a
// user's arrangement.
function reconcile(stored: SidebarMode[], all: readonly SidebarMode[]): SidebarMode[] {
  const known = stored.filter((m) => all.includes(m));
  const missing = all.filter((m) => !known.includes(m));
  return [...known, ...missing];
}

function sameOrder(a: SidebarMode[], b: SidebarMode[]): boolean {
  return a.length === b.length && a.every((m, i) => m === b[i]);
}

/** Which side of the target section the dragged one is dropped on. */
export type DropSide = "before" | "after";

// Module-level store so the rail, the tabs, the collapsed rail and the home
// rail all share one live order — a drag-reorder in any of them is reflected
// everywhere immediately, not just after a remount re-reads localStorage.
//
// The store holds the *persisted* arrangement only. Reconciliation against the
// live section list happens per-read, because plugin-contributed sections
// appear asynchronously (once `/api/plugins` resolves) and must slot in without
// a reload.
let order: SidebarMode[] | null = null;
const listeners = new Set<() => void>();

function loadPersisted(): SidebarMode[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as SidebarMode[]) : [];
  } catch {
    return [];
  }
}

function snapshot(): SidebarMode[] {
  if (order === null) order = loadPersisted();
  return order;
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

function persist(next: SidebarMode[]): void {
  order = next;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* ignore quota / privacy-mode failures */
    }
  }
  listeners.forEach((cb) => cb());
}

function reorderStore(
  dragged: SidebarMode,
  target: SidebarMode,
  side: DropSide,
  all: readonly SidebarMode[],
): void {
  // Persist the reconciled list, so sections the stored order never saw keep
  // the slot they are currently rendered in.
  const prev = reconcile(snapshot(), all);
  if (dragged === target) return;
  if (prev.indexOf(dragged) === -1 || prev.indexOf(target) === -1) return;
  const next = prev.filter((m) => m !== dragged);
  const ti = next.indexOf(target);
  next.splice(side === "after" ? ti + 1 : ti, 0, dragged);
  if (!sameOrder(next, prev)) persist(next);
}

/**
 * Persisted, user-reorderable ordering of sidebar sections. `all` is the
 * canonical list of every known section, enabled or not, so a section keeps its
 * slot when toggled off and back on; pass a memoised reference. Sections absent
 * from the persisted order (newly shipped, or contributed by a plugin that has
 * just loaded) are appended rather than wiping the user's arrangement. Returns
 * the reconciled order plus a `reorder` mover that drops the dragged section on
 * either side of the target.
 */
export function useSectionOrder(all: readonly SidebarMode[]) {
  const persisted = useSyncExternalStore(subscribe, snapshot, snapshot);
  const value = useMemo(() => reconcile(persisted, all), [persisted, all]);
  const reorder = useCallback(
    (dragged: SidebarMode, target: SidebarMode, side: DropSide = "before") => {
      reorderStore(dragged, target, side, all);
    },
    [all],
  );
  return { order: value, reorder };
}
