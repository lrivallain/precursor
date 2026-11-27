// A swipe that starts on a quick-actions label scrolls the menu. The labels sit
// outside the scrolling list (so they are never clipped sideways), so the list
// can't see that gesture by itself; and a swipe must not count as a tap.

export interface LabelSwipe {
  /** The finger that started it: other pointers (a second finger, a mouse) are ignored. */
  pointerId: number;
  startX: number;
  startY: number;
  startScroll: number;
  moved: boolean;
}

/** Movement (px) before a press on a label becomes a swipe rather than a tap. */
export const SWIPE_THRESHOLD = 8;

export function startSwipe(pointerId: number, x: number, y: number, scrollTop: number): LabelSwipe {
  return { pointerId, startX: x, startY: y, startScroll: scrollTop, moved: false };
}

/** The swipe `pointerId` belongs to, if any. */
export function swipeOf(swipe: LabelSwipe | null, pointerId: number): LabelSwipe | null {
  return swipe && swipe.pointerId === pointerId ? swipe : null;
}

/**
 * The menu's new scrollTop for a finger now at (x, y), or null while it is
 * still a tap. Movement in any direction makes it a swipe (so no tap fires);
 * only the vertical part scrolls.
 */
export function swipeScrollTop(swipe: LabelSwipe, x: number, y: number): number | null {
  const dy = y - swipe.startY;
  if (!swipe.moved && Math.hypot(x - swipe.startX, dy) < SWIPE_THRESHOLD) return null;
  swipe.moved = true;
  return Math.max(0, swipe.startScroll - dy);
}
