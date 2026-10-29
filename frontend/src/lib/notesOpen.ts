import type { ReminderContainer } from "./types";

const NOTES_OPEN_EVENT = "precursor:notes-open";

export function openNotes(container: ReminderContainer, id: number): void {
  window.dispatchEvent(
    new CustomEvent(NOTES_OPEN_EVENT, {
      detail: { container, id },
    }),
  );
}

export function subscribeNotesOpen(
  container: ReminderContainer,
  id: number,
  handler: () => void,
): () => void {
  const listener = (event: Event) => {
    const detail = (event as CustomEvent<{ container: ReminderContainer; id: number }>).detail;
    if (detail.container === container && detail.id === id) handler();
  };
  window.addEventListener(NOTES_OPEN_EVENT, listener);
  return () => window.removeEventListener(NOTES_OPEN_EVENT, listener);
}

/** Add `addition` under `existing`, a blank line apart. */
export function joinNote(existing: string, addition: string): string {
  const base = existing.replace(/\s+$/, "");
  return base ? `${base}\n\n${addition}` : addition;
}

/** Text appended to an open pad; the nonce makes a repeat of the same text land. */
export interface NoteAppendRequest {
  text: string;
  nonce: number;
}

const DETACHED_NOTES_APPEND_EVENT = "precursor:detached-notes-append";

interface DetachedAppendDetail {
  container: ReminderContainer;
  id: number;
  text: string;
}

/** Append to the popped-out notes window of a conversation (see DetachedNotesController). */
export function appendToDetachedNotes(container: ReminderContainer, id: number, text: string): void {
  window.dispatchEvent(
    new CustomEvent<DetachedAppendDetail>(DETACHED_NOTES_APPEND_EVENT, {
      detail: { container, id, text },
    }),
  );
}

export function subscribeDetachedNotesAppend(
  container: ReminderContainer,
  id: number,
  handler: (text: string) => void,
): () => void {
  const listener = (event: Event) => {
    const detail = (event as CustomEvent<DetachedAppendDetail>).detail;
    if (detail.container === container && detail.id === id) handler(detail.text);
  };
  window.addEventListener(DETACHED_NOTES_APPEND_EVENT, listener);
  return () => window.removeEventListener(DETACHED_NOTES_APPEND_EVENT, listener);
}
