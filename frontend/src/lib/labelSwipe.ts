// A swipe that starts on a quick-actions label scrolls the menu. The labels sit
// outside the scrolling list (so they are never clipped sideways), so the list
// can't see that gesture by itself; and a swipe must not count as a tap.

export interface LabelSwipe {
  startY: number;
  startScroll: number;
  moved: boolean;
}

/** Movement (px) before a press on a label becomes a swipe rather than a tap. */
export const SWIPE_THRESHOLD = 8;

export function startSwipe(y: number, scrollTop: number): LabelSwipe {
  return { startY: y, startScroll: scrollTop, moved: false };
}

/** The menu's new scrollTop for a finger now at `y`, or null while it is still a tap. */
export function swipeScrollTop(swipe: LabelSwipe, y: number): number | null {
  const dy = y - swipe.startY;
  if (!swipe.moved && Math.abs(dy) < SWIPE_THRESHOLD) return null;
  swipe.moved = true;
  return Math.max(0, swipe.startScroll - dy);
}
