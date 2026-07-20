import { useCallback, useSyncExternalStore } from "react";

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
let order: SidebarMode[] | null = null;
const listeners = new Set<() => void>();

function loadInitial(all: readonly SidebarMode[]): SidebarMode[] {
  const fallback = [...all];
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return fallback;
    return reconcile(parsed as SidebarMode[], all);
  } catch {
    return fallback;
  }
}

function ensureInit(all: readonly SidebarMode[]): void {
  if (order === null) order = loadInitial(all);
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

function reorderStore(dragged: SidebarMode, target: SidebarMode, side: DropSide): void {
  const prev = order ?? [];
  if (dragged === target) return;
  if (prev.indexOf(dragged) === -1 || prev.indexOf(target) === -1) return;
  const next = prev.filter((m) => m !== dragged);
  const ti = next.indexOf(target);
  next.splice(side === "after" ? ti + 1 : ti, 0, dragged);
  if (!sameOrder(next, prev)) persist(next);
}

/**
 * Persisted, user-reorderable ordering of sidebar sections. `all` must be a
 * stable reference (a module-level constant) — it is the canonical list of
 * every section, enabled or not, so a section keeps its slot when toggled off
 * and back on. Returns the reconciled order plus a `reorder` mover that drops
 * the dragged section on either side of the target.
 */
export function useSectionOrder(all: readonly SidebarMode[]) {
  ensureInit(all);
  const value = useSyncExternalStore(
    subscribe,
    () => order as SidebarMode[],
    () => order as SidebarMode[],
  );
  const reorder = useCallback(
    (dragged: SidebarMode, target: SidebarMode, side: DropSide = "before") => {
      reorderStore(dragged, target, side);
    },
    [],
  );
  return { order: value, reorder };
}
