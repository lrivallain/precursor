import { useCallback, useEffect, useState } from "react";

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

/**
 * Persisted, user-reorderable ordering of sidebar sections. `all` must be a
 * stable reference (a module-level constant) — it is the canonical list of
 * every section, enabled or not, so a section keeps its slot when toggled off
 * and back on. Returns the reconciled order plus a `reorder` mover that drops
 * the dragged section on either side of the target.
 */
export function useSectionOrder(all: readonly SidebarMode[]) {
  const [order, setOrder] = useState<SidebarMode[]>(() => {
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
  });

  // Reconcile if the canonical set changes (a section is added or removed in a
  // later build) so the persisted order never drifts out of sync.
  useEffect(() => {
    setOrder((prev) => {
      const next = reconcile(prev, all);
      return sameOrder(next, prev) ? prev : next;
    });
  }, [all]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(order));
  }, [order]);

  const reorder = useCallback(
    (dragged: SidebarMode, target: SidebarMode, side: DropSide = "before") => {
      setOrder((prev) => {
        if (dragged === target) return prev;
        if (prev.indexOf(dragged) === -1 || prev.indexOf(target) === -1) return prev;
        const next = prev.filter((m) => m !== dragged);
        const ti = next.indexOf(target);
        next.splice(side === "after" ? ti + 1 : ti, 0, dragged);
        return sameOrder(next, prev) ? prev : next;
      });
    },
    [],
  );

  return { order, reorder };
}
