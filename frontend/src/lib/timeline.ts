import type { TurnIndexItem } from "./types";

/** "rewind": keep the chosen turn and drop what follows. "edit": drop it too and re-edit its prompt. */
export type RewindMode = "rewind" | "edit";

export interface RewindCut {
  /** First message deleted: the prompt opening the first dropped turn. */
  fromId: number;
  /** Number of turns the cut drops. */
  droppedTurns: number;
}

/** Where a rewind on `turns[index]` cuts the transcript, or null when nothing would go. */
export function rewindCut(
  turns: readonly TurnIndexItem[],
  index: number,
  mode: RewindMode,
): RewindCut | null {
  const first = mode === "edit" ? index : index + 1;
  const turn = turns[first];
  if (index < 0 || !turn) return null;
  return { fromId: turn.message_id, droppedTurns: turns.length - first };
}

export interface TimelineView {
  /** Turn being read: the last whose prompt passed the top edge (the newest when at the bottom). */
  current: number;
  /** First and last turn with any part on screen (-1 when none is loaded). */
  first: number;
  last: number;
  /** Parked at (or near) the bottom of the transcript. */
  atLatest: boolean;
}

export const EMPTY_VIEW: TimelineView = { current: -1, first: -1, last: -1, atLatest: true };

// A prompt counts as "passed" once it is this close to the top edge, so the
// turn being read is the one whose prompt was just scrolled under.
const CURRENT_EDGE_PX = 48;
const NEAR_BOTTOM_PX = 80;

/**
 * Locate the turns on screen. `anchorTops` maps a prompt's message id to its
 * offset from the top of the scroll viewport; older turns not loaded yet are
 * simply absent.
 */
export function measureView(
  turns: readonly TurnIndexItem[],
  anchorTops: ReadonlyMap<number, number>,
  box: { scrollTop: number; scrollHeight: number; clientHeight: number },
): TimelineView {
  const atLatest = box.scrollHeight - box.scrollTop - box.clientHeight < NEAR_BOTTOM_PX;
  const contentBottom = box.scrollHeight - box.scrollTop;
  let current = -1;
  let first = -1;
  let last = -1;
  let firstLoaded = -1;
  for (let i = 0; i < turns.length; i++) {
    const top = anchorTops.get(turns[i].message_id);
    if (top === undefined) continue;
    if (firstLoaded < 0) firstLoaded = i;
    let end = contentBottom;
    for (let j = i + 1; j < turns.length; j++) {
      const next = anchorTops.get(turns[j].message_id);
      if (next !== undefined) {
        end = next;
        break;
      }
    }
    if (top <= CURRENT_EDGE_PX) current = i;
    if (top < box.clientHeight && end > 0) {
      if (first < 0) first = i;
      last = i;
    }
  }
  if (current < 0) current = first >= 0 ? first : firstLoaded;
  // Parked at the bottom, the reader is on the newest turn even when it sits
  // low on screen under a short previous one.
  if (atLatest && last === turns.length - 1) current = last;
  return { current, first, last, atLatest };
}

export function sameView(a: TimelineView, b: TimelineView): boolean {
  return a.current === b.current && a.first === b.first && a.last === b.last && a.atLatest === b.atLatest;
}
