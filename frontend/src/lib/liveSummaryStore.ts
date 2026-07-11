import { useSyncExternalStore } from "react";

/**
 * A meeting-summary panel that has been popped out of the Live view into its own
 * OS window. Held at app level (see {@link DetachedLiveSummaryHost}) so the
 * window survives navigation away from the live session in the main tab.
 */
export interface DetachedSummary {
  id: string;
  sessionId: number;
  topicId: number | null;
  topicTitle: string | null;
  title: string;
  initialText: string;
}

let sessions: DetachedSummary[] = [];
const listeners = new Set<() => void>();
let counter = 0;

function emit(): void {
  for (const l of listeners) l();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export const liveSummaryStore = {
  /** Open a detached summary window; returns its id. */
  open(session: Omit<DetachedSummary, "id">): string {
    const id = `live-summary-${++counter}`;
    sessions = [...sessions, { ...session, id }];
    emit();
    return id;
  },
  close(id: string): void {
    const next = sessions.filter((s) => s.id !== id);
    if (next.length === sessions.length) return;
    sessions = next;
    emit();
  },
  snapshot(): DetachedSummary[] {
    return sessions;
  },
};

export function useDetachedSummaries(): DetachedSummary[] {
  return useSyncExternalStore(subscribe, liveSummaryStore.snapshot, liveSummaryStore.snapshot);
}
