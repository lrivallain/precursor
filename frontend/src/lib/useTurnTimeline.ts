import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { EMPTY_VIEW, measureView, sameView, type TimelineView } from "./timeline";
import { inCut } from "./useRewind";
import type { Message, TurnIndexItem } from "./types";

export interface UseTurnTimelineOptions {
  /** Identity of the conversation; a change refetches the index. */
  resetKey: string;
  listTurns: () => Promise<TurnIndexItem[]>;
  scrollRef: React.RefObject<HTMLDivElement | null>;
  /** The rendered transcript; re-measured whenever it changes. */
  messages: readonly Message[];
  streaming: boolean;
  /** Page older history in until this message is in the window. */
  ensureLoaded: (messageId: number) => Promise<boolean>;
  /** Prompt ids hidden by a pending rewind. */
  hidden: { fromId: number; throughId: number } | null;
}

export interface TurnTimeline {
  turns: TurnIndexItem[];
  view: TimelineView;
  /** Scroll turn `index` into view, loading older pages first if needed. */
  jumpTo: (index: number) => Promise<void>;
}

/** Attribute on the element of each turn's prompt, holding its message id. */
export const TURN_ANCHOR_ATTR = "data-turn-anchor";

function anchorTops(box: HTMLElement): Map<number, number> {
  const boxTop = box.getBoundingClientRect().top;
  const tops = new Map<number, number>();
  box.querySelectorAll<HTMLElement>(`[${TURN_ANCHOR_ATTR}]`).forEach((el) => {
    tops.set(Number(el.getAttribute(TURN_ANCHOR_ATTR)), el.getBoundingClientRect().top - boxTop);
  });
  return tops;
}

function findAnchor(box: HTMLElement, messageId: number): HTMLElement | null {
  return box.querySelector<HTMLElement>(`[${TURN_ANCHOR_ATTR}="${messageId}"]`);
}

const nextFrame = () => new Promise<void>((r) => requestAnimationFrame(() => r()));

/**
 * The turn index and scroll position behind the transcript timeline rail. The
 * index comes from the server because the transcript is windowed: the rail
 * covers turns that haven't been paged in yet.
 */
export function useTurnTimeline({
  resetKey,
  listTurns,
  scrollRef,
  messages,
  streaming,
  ensureLoaded,
  hidden,
}: UseTurnTimelineOptions): TurnTimeline {
  const [allTurns, setAllTurns] = useState<TurnIndexItem[]>([]);
  const [view, setView] = useState<TimelineView>(EMPTY_VIEW);
  const listTurnsRef = useRef(listTurns);
  listTurnsRef.current = listTurns;

  // Refetch when the persisted tail changes (a turn landed, a rewind, a
  // delete); skip mid-stream, the index catches up once the reply is saved.
  const tailId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) if (messages[i].id > 0) return messages[i].id;
    return 0;
  }, [messages]);
  const persistedCount = useMemo(() => messages.filter((m) => m.id > 0).length, [messages]);
  useEffect(() => setAllTurns([]), [resetKey]);
  useEffect(() => {
    if (streaming) return;
    let cancelled = false;
    listTurnsRef
      .current()
      .then((turns) => {
        if (!cancelled) setAllTurns(turns);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [resetKey, tailId, persistedCount, streaming]);

  // Nothing past the transcript's tail is a turn yet, nor is anything a
  // pending rewind hides; this also covers the index lagging a rewind.
  const turns = useMemo(
    () =>
      allTurns.filter((t) => t.message_id <= tailId && !inCut(t.message_id, hidden)),
    [allTurns, tailId, hidden],
  );
  const turnsRef = useRef(turns);
  turnsRef.current = turns;

  const measure = useCallback(() => {
    const box = scrollRef.current;
    if (!box || turnsRef.current.length === 0) {
      setView((prev) => (sameView(prev, EMPTY_VIEW) ? prev : EMPTY_VIEW));
      return;
    }
    const next = measureView(turnsRef.current, anchorTops(box), box);
    setView((prev) => (sameView(prev, next) ? prev : next));
  }, [scrollRef]);

  useLayoutEffect(measure, [measure, messages, turns]);

  useEffect(() => {
    const box = scrollRef.current;
    if (!box) return;
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        measure();
      });
    };
    box.addEventListener("scroll", onScroll, { passive: true });
    const resize = new ResizeObserver(onScroll);
    resize.observe(box);
    return () => {
      box.removeEventListener("scroll", onScroll);
      resize.disconnect();
      if (frame) cancelAnimationFrame(frame);
    };
  }, [scrollRef, measure, resetKey]);

  const jumpTo = useCallback(
    async (index: number) => {
      const turn = turnsRef.current[index];
      const box = scrollRef.current;
      if (!turn || !box) return;
      let el = findAnchor(box, turn.message_id);
      if (!el) {
        if (!(await ensureLoaded(turn.message_id))) return;
        // Wait for the older page to render.
        for (let i = 0; i < 20 && !el; i++) {
          await nextFrame();
          el = findAnchor(box, turn.message_id);
        }
        if (!el) return;
      }
      box.scrollTop += el.getBoundingClientRect().top - box.getBoundingClientRect().top - 12;
      if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        el.animate(
          [
            { boxShadow: "0 0 0 2px var(--accent)", borderRadius: "12px" },
            { boxShadow: "0 0 0 2px transparent", borderRadius: "12px" },
          ],
          { duration: 1100, easing: "ease-out" },
        );
      }
    },
    [scrollRef, ensureLoaded],
  );

  return { turns, view, jumpTo };
}
