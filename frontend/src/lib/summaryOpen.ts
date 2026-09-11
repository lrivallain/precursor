/**
 * Cross-component trigger for the topic summary panel.
 *
 * The panel lives in `ChatPanel` (it belongs to the transcript) but is reached
 * from the shared topic header in `App`, which doesn't own its state. Same
 * pattern as `notesOpen.ts`: the header fires an event, the panel reacts.
 */

const SUMMARY_TOGGLE_EVENT = "precursor:summary-toggle";

export function toggleTopicSummary(topicId: number): void {
  window.dispatchEvent(
    new CustomEvent(SUMMARY_TOGGLE_EVENT, { detail: { topicId } }),
  );
}

export function subscribeTopicSummaryToggle(
  topicId: number,
  handler: () => void,
): () => void {
  const listener = (event: Event) => {
    const detail = (event as CustomEvent<{ topicId: number }>).detail;
    if (detail.topicId === topicId) handler();
  };
  window.addEventListener(SUMMARY_TOGGLE_EVENT, listener);
  return () => window.removeEventListener(SUMMARY_TOGGLE_EVENT, listener);
}
