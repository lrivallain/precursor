import { useCallback, useLayoutEffect, useRef } from "react";

// How close to the bottom (px) still counts as "parked at the bottom", so new
// turns keep following without yanking a user who has scrolled up.
const NEAR_BOTTOM_PX = 80;
// How close to the top (px) triggers loading the next older page.
const NEAR_TOP_PX = 120;

export interface ChatScroll {
  scrollRef: React.RefObject<HTMLDivElement | null>;
  onScroll: () => void;
  /**
   * Snapshot the current scroll height immediately before prepending older
   * content. The layout effect then keeps the same message under the viewport
   * so the list doesn't jump when items are added above.
   */
  captureTopAnchor: () => void;
  /** Re-pin to the bottom (e.g. when the user sends a new message). */
  pinToBottom: () => void;
  scrollToBottom: () => void;
  pinnedRef: React.MutableRefObject<boolean>;
}

/**
 * Reverse-infinite-scroll behaviour for chat transcripts: stay glued to the
 * bottom while the user is parked there, preserve viewport position when older
 * messages are prepended, and fire `onReachTop` when the user scrolls near the
 * top so the caller can load an older page.
 *
 * `deps` must have a stable length across renders (typically
 * `[messages, pendingContent]`); the bottom-glue / position-restore runs after
 * every change to them.
 */
export function useChatScroll(
  deps: readonly unknown[],
  onReachTop: () => void,
): ChatScroll {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  // Captured scrollHeight from just before a prepend; null when not prepending.
  const anchorRef = useRef<number | null>(null);

  const captureTopAnchor = useCallback(() => {
    const box = scrollRef.current;
    anchorRef.current = box ? box.scrollHeight : null;
  }, []);

  const pinToBottom = useCallback(() => {
    pinnedRef.current = true;
  }, []);

  const scrollToBottom = useCallback(() => {
    const box = scrollRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, []);

  useLayoutEffect(() => {
    const box = scrollRef.current;
    if (!box) return;
    if (anchorRef.current != null) {
      // Older content was prepended: shift down by the height it added so the
      // viewport stays on the message the user was reading.
      box.scrollTop += box.scrollHeight - anchorRef.current;
      anchorRef.current = null;
      return;
    }
    if (pinnedRef.current) box.scrollTop = box.scrollHeight;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const onScroll = useCallback(() => {
    const box = scrollRef.current;
    if (!box) return;
    pinnedRef.current =
      box.scrollHeight - box.scrollTop - box.clientHeight < NEAR_BOTTOM_PX;
    if (box.scrollTop < NEAR_TOP_PX) onReachTop();
  }, [onReachTop]);

  return {
    scrollRef,
    onScroll,
    captureTopAnchor,
    pinToBottom,
    scrollToBottom,
    pinnedRef,
  };
}
