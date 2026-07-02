import { useSyncExternalStore } from "react";
import type { ConvKind } from "./streamStore";
import type { NoteDraftAttachment } from "./types";

export type DetachedKind = "notes" | "gh-create" | "gh-update" | "gh-close";

/**
 * A panel that has been popped out of the main app into its own OS window.
 *
 * The session is fully self-describing so the app-level host can keep rendering
 * (and the controller can keep acting on) the *original* container even after
 * the user navigates away from it in the main tab. Everything the controllers
 * need is captured at pop-out time — they never reach back into the topic-scoped
 * component that spawned them.
 */
export interface DetachedSession {
  id: string;
  kind: DetachedKind;
  container: ConvKind;
  containerId: number;
  /** Window + header title. */
  title: string;
  subtitle?: string;

  // --- notes ---
  hasIssue?: boolean;
  allowPostComment?: boolean;
  initialText: string;
  initialAttachments?: NoteDraftAttachment[];

  // --- gh command draft (gh-create / gh-update / gh-close) ---
  initialTitle?: string;
  titleLabel?: string;
  bodyPlaceholder?: string;
  bodyRequired?: boolean;
  sendLabel?: string;
  postingLabel?: string;
  confirmHint?: string;
}

let sessions: DetachedSession[] = [];
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

export const detachedDraftStore = {
  /** Open (or focus) a detached session, returning its id. */
  open(session: Omit<DetachedSession, "id">): string {
    const id = `detached-${++counter}`;
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
  /** True when a detached window already exists for this container + kind. */
  has(container: ConvKind, containerId: number, kind?: DetachedKind): boolean {
    return sessions.some(
      (s) =>
        s.container === container &&
        s.containerId === containerId &&
        (kind === undefined || s.kind === kind),
    );
  },
  snapshot(): DetachedSession[] {
    return sessions;
  },
};

/** Subscribe a component to the list of detached sessions. */
export function useDetachedDrafts(): DetachedSession[] {
  return useSyncExternalStore(subscribe, detachedDraftStore.snapshot, detachedDraftStore.snapshot);
}

/**
 * Notified whenever a detached controller mutates the persisted note draft for a
 * container (save / append / clear). Lets the originating panel keep its
 * "saved notes draft" banner live instead of waiting for a manual refresh.
 */
type DraftChangeListener = (container: ConvKind, containerId: number) => void;
const draftChangeListeners = new Set<DraftChangeListener>();

/** Broadcast that the persisted note draft for a container changed. */
export function notifyNoteDraftChanged(container: ConvKind, containerId: number): void {
  for (const l of draftChangeListeners) l(container, containerId);
}

/** Subscribe to persisted note-draft changes; returns an unsubscribe callback. */
export function subscribeNoteDraftChanges(listener: DraftChangeListener): () => void {
  draftChangeListeners.add(listener);
  return () => {
    draftChangeListeners.delete(listener);
  };
}
