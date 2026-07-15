import { useCallback, useMemo, useState } from "react";

/**
 * Selection state for a sidebar list's "select multiple" mode. Kept
 * intentionally generic (ids are plain numbers) so the same hook powers the
 * chats, agents and live-session lists, which each bulk-archive their selection.
 */
export interface MultiSelect {
  /** Whether selection mode is active (checkboxes shown, clicks toggle). */
  active: boolean;
  /** Currently selected ids. */
  selected: Set<number>;
  count: number;
  isSelected: (id: number) => boolean;
  toggle: (id: number) => void;
  /** Select every id, or clear when they're all already selected. */
  toggleAll: (ids: number[]) => void;
  /** Enter selection mode, optionally pre-selecting one id. */
  enter: (id?: number) => void;
  /** Leave selection mode and drop the selection. */
  exit: () => void;
  /** Drop the selection but stay in selection mode. */
  clear: () => void;
  /** Drop ids no longer present (e.g. after items were archived/removed). */
  prune: (ids: number[]) => void;
}

export function useMultiSelect(): MultiSelect {
  const [active, setActive] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  const toggle = useCallback((id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleAll = useCallback((ids: number[]) => {
    setSelected((prev) => {
      const allSelected = ids.length > 0 && ids.every((id) => prev.has(id));
      return allSelected ? new Set() : new Set(ids);
    });
  }, []);

  const enter = useCallback((id?: number) => {
    setActive(true);
    setSelected(id != null ? new Set([id]) : new Set());
  }, []);

  const exit = useCallback(() => {
    setActive(false);
    setSelected(new Set());
  }, []);

  const clear = useCallback(() => setSelected(new Set()), []);

  const prune = useCallback((ids: number[]) => {
    const keep = new Set(ids);
    setSelected((prev) => {
      const next = new Set([...prev].filter((id) => keep.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, []);

  return useMemo(
    () => ({
      active,
      selected,
      count: selected.size,
      isSelected: (id: number) => selected.has(id),
      toggle,
      toggleAll,
      enter,
      exit,
      clear,
      prune,
    }),
    [active, selected, toggle, toggleAll, enter, exit, clear, prune],
  );
}
