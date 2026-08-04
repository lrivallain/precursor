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
