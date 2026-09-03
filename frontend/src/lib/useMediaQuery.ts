import { useSyncExternalStore } from "react";

/**
 * Viewport width at (and below) which the shell switches to its single-pane
 * layout: the sidebar stops being a fixed column and becomes an off-canvas
 * drawer, so the main pane gets the whole screen. Matches Tailwind's `md`
 * breakpoint so `md:` utilities and this hook stay in lockstep.
 */
export const NARROW_QUERY = "(max-width: 767px)";

interface Store {
  subscribe: (cb: () => void) => () => void;
  getSnapshot: () => boolean;
}

// One store per query, shared by every consumer, so N components mounting the
// same breakpoint don't each attach their own MediaQueryList listener — and so
// they all re-render off a single, already-consistent snapshot.
const stores = new Map<string, Store>();

function storeFor(query: string): Store {
  const existing = stores.get(query);
  if (existing) return existing;

  const mql =
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(query)
      : null;
  let current = mql?.matches ?? false;
  const listeners = new Set<() => void>();

  mql?.addEventListener("change", (e) => {
    current = e.matches;
    listeners.forEach((cb) => cb());
  });

  const store: Store = {
    subscribe(cb) {
      listeners.add(cb);
      return () => {
        listeners.delete(cb);
      };
    },
    getSnapshot: () => current,
  };
  stores.set(query, store);
  return store;
}

/** Subscribe to a CSS media query. Re-renders when the match state flips. */
export function useMediaQuery(query: string): boolean {
  const store = storeFor(query);
  return useSyncExternalStore(store.subscribe, store.getSnapshot, () => false);
}

/** True on phone-sized viewports, where the shell collapses to one pane. */
export function useIsNarrow(): boolean {
  return useMediaQuery(NARROW_QUERY);
}

/**
 * Width below which the workspace can't host its three panes at once — a file
 * tree, an editor and a 24rem assistant leave the editor unusably thin well
 * before the phone breakpoint. Above it the assistant docks beside the editor;
 * below it, it starts stowed and expands over the workspace.
 */
export const WORKSPACE_PANES_QUERY = "(max-width: 1023px)";
