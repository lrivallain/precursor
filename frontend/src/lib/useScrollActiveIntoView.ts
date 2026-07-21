import { useCallback, useRef } from "react";

/**
 * Returns a callback ref to attach to the currently-active item in a scrollable
 * list. Whenever the active key changes — e.g. an item reached from the URL, the
 * command palette or a search — the referenced element is scrolled into view
 * (block: "nearest"), so the selection is never left off-screen. Items already
 * visible aren't moved, and re-renders that don't change the active key (unread
 * badges, streaming dots, …) never trigger a scroll.
 *
 * Attach the ref only to the active element, e.g.
 * `ref={isActive ? activeItemRef : undefined}`.
 */
export function useScrollActiveIntoView<T extends HTMLElement = HTMLElement>(
  activeKey: string | number | null | undefined,
): (el: T | null) => void {
  const scrolledKey = useRef<string | number | null | undefined>(undefined);
  return useCallback(
    (el: T | null) => {
      if (!el || activeKey == null) return;
      // Guard against re-scrolling for the same selection (e.g. the active item
      // remounting while an in-list search filters the surrounding rows).
      if (scrolledKey.current === activeKey) return;
      scrolledKey.current = activeKey;
      el.scrollIntoView({ block: "nearest" });
    },
    [activeKey],
  );
}
