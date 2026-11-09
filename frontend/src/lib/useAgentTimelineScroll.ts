import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import type { AgentPane } from "../components/AgentResults";

// How many timeline segments are mounted at once; older ones are kept out of
// the DOM until the user scrolls toward the top, where another window's worth
// is revealed. Keeps very long agent runs from rendering thousands of nodes up
// front.
export const AGENT_SEGMENT_WINDOW = 40;

export interface TimelineScrollOptions {
  agentId: number | null;
  /** Changes whenever the transcript grows (the events array). */
  content: unknown;
  showPending: boolean;
  pane: AgentPane;
  segmentCount: number;
  /** Segment index of each exchange's prompt. */
  exchangeStart: Map<number, number>;
  /** Switch to the Activity pane. */
  onShowActivity: () => void;
  /** The result version on screen; a different one reads from its top. */
  shownVersion: number | null;
}

export interface TimelineScroll {
  scrollRef: RefObject<HTMLDivElement | null>;
  innerRef: RefObject<HTMLDivElement | null>;
  resultScrollRef: RefObject<HTMLDivElement | null>;
  /** Absolute index of the first mounted segment. */
  windowStart: number;
  onScroll: () => void;
  /** Stop following the newest step (the user is reading back). */
  unpin: () => void;
  /** Mount segments from `at` onward, keeping what's already shown. */
  revealFrom: (at: number) => void;
  /** "Show in activity": bring an exchange into view, prompt at the top. */
  showInActivity: (exchange: number) => void;
  /** Bring an exchange's prompt into the render window before jumping to it. */
  ensureTurnMounted: (exchange: number) => Promise<boolean>;
}

// Reverse-windowing of the rendered timeline: only segments from `windowStart`
// onward are mounted. The start is a stable absolute index so appending new
// segments never shifts what's already on screen; scrolling toward the top
// lowers it to reveal older history.
function useSegmentWindow(segmentCount: number, pinnedRef: RefObject<boolean>, scrollRef: RefObject<HTMLDivElement | null>) {
  const [windowStart, setWindowStart] = useState(0);
  const windowStartRef = useRef(0);
  // Captured scrollHeight from just before revealing older segments, so the
  // layout effect can keep the viewport anchored after they mount.
  const revealAnchorRef = useRef<number | null>(null);
  useEffect(() => {
    windowStartRef.current = windowStart;
  }, [windowStart]);
  useLayoutEffect(() => {
    if (revealAnchorRef.current == null) return;
    const box = scrollRef.current;
    if (box) box.scrollTop += box.scrollHeight - revealAnchorRef.current;
    revealAnchorRef.current = null;
  }, [windowStart, scrollRef]);
  // While the user is parked at the bottom, keep the window bounded to the most
  // recent page so long runs don't accumulate unbounded DOM. While scrolled up,
  // leave the start fixed (only clamped) so newly appended steps never shift
  // the history being read.
  useEffect(() => {
    setWindowStart((start) =>
      pinnedRef.current
        ? Math.max(0, segmentCount - AGENT_SEGMENT_WINDOW)
        : Math.min(start, Math.max(0, segmentCount - 1)),
    );
  }, [segmentCount, pinnedRef]);
  return { windowStart, setWindowStart, windowStartRef, revealAnchorRef };
}

// "Show in activity" from a result version: bring its exchange into the render
// window, then scroll its prompt to the top. Otherwise, back on the timeline
// rejoins the newest step.
function useExchangeJump(
  opts: TimelineScrollOptions,
  refs: { innerRef: RefObject<HTMLDivElement | null>; pinnedRef: RefObject<boolean> },
  windowStart: number,
  setWindowStart: (at: number) => void,
  scrollToBottom: () => void,
) {
  const { pane, exchangeStart, onShowActivity } = opts;
  const { innerRef, pinnedRef } = refs;
  const jumpRef = useRef<number | null>(null);
  useEffect(() => {
    if (pane !== "activity" || jumpRef.current != null) return;
    pinnedRef.current = true;
    requestAnimationFrame(scrollToBottom);
  }, [pane, scrollToBottom, pinnedRef]);
  const showInActivity = useCallback(
    (exchange: number) => {
      jumpRef.current = exchange;
      pinnedRef.current = false;
      onShowActivity();
    },
    [onShowActivity, pinnedRef],
  );
  useEffect(() => {
    const target = jumpRef.current;
    if (pane !== "activity" || target == null) return;
    const at = exchangeStart.get(target);
    if (at == null) {
      jumpRef.current = null;
      return;
    }
    if (at < windowStart) {
      setWindowStart(at);
      return;
    }
    jumpRef.current = null;
    requestAnimationFrame(() => {
      innerRef.current?.querySelector(`[data-exchange="${target}"]`)?.scrollIntoView({ block: "start" });
    });
  }, [pane, exchangeStart, windowStart, setWindowStart, innerRef]);
  return showInActivity;
}

/**
 * Scrolling of the agent timeline: follow the newest step while the user is
 * parked at the bottom (never yank them away mid-scroll), window long
 * transcripts, and jump to an exchange on request.
 */
export function useAgentTimelineScroll(opts: TimelineScrollOptions): TimelineScroll {
  const { agentId, content, showPending, pane, exchangeStart } = opts;
  const scrollRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const resultScrollRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  const { windowStart, setWindowStart, windowStartRef, revealAnchorRef } = useSegmentWindow(
    opts.segmentCount,
    pinnedRef,
    scrollRef,
  );

  // Snap the scroll container to its absolute bottom. Driving scrollTop
  // directly (rather than scrollIntoView on an anchor) means height that lands
  // late — streaming text, markdown/code reflow, images — can't leave us short.
  const scrollToBottom = useCallback(() => {
    const box = scrollRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, []);

  // While pinned, stay glued to the bottom as the content height changes. A
  // ResizeObserver catches every reflow, including ones that land after React
  // has committed the events update. The timeline's container only exists on
  // the Activity pane, so re-attach whenever it (re)mounts.
  useEffect(() => {
    const inner = innerRef.current;
    if (!inner) return;
    const ro = new ResizeObserver(() => {
      if (pinnedRef.current) scrollToBottom();
    });
    ro.observe(inner);
    return () => ro.disconnect();
  }, [scrollToBottom, pane]);

  // New steps: follow to the bottom while pinned, once layout has settled.
  useEffect(() => {
    if (pinnedRef.current) requestAnimationFrame(scrollToBottom);
  }, [content, showPending, scrollToBottom]);

  // Jump straight to the bottom (and re-pin) when switching agents.
  useEffect(() => {
    pinnedRef.current = true;
    requestAnimationFrame(scrollToBottom);
  }, [agentId, scrollToBottom]);

  const showInActivity = useExchangeJump(opts, { innerRef, pinnedRef }, windowStart, setWindowStart, scrollToBottom);

  const shownVersion = opts.shownVersion;
  useEffect(() => {
    if (resultScrollRef.current) resultScrollRef.current.scrollTop = 0;
  }, [shownVersion, pane]);

  const onScroll = useCallback(() => {
    const box = scrollRef.current;
    if (!box) return;
    pinnedRef.current = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    // Near the top with older segments still hidden: reveal the next window,
    // anchoring the viewport so it doesn't jump as boxes mount above.
    if (box.scrollTop < 120 && windowStartRef.current > 0) {
      revealAnchorRef.current = box.scrollHeight;
      setWindowStart((s) => Math.max(0, s - AGENT_SEGMENT_WINDOW));
    }
  }, [windowStartRef, revealAnchorRef, setWindowStart]);

  const unpin = useCallback(() => {
    pinnedRef.current = false;
  }, []);
  const revealFrom = useCallback((at: number) => setWindowStart((start) => Math.min(start, at)), [setWindowStart]);
  const ensureTurnMounted = useCallback(
    async (exchange: number) => {
      const at = exchangeStart.get(exchange);
      if (at == null) return false;
      if (at < windowStartRef.current) {
        pinnedRef.current = false;
        setWindowStart(at);
      }
      return true;
    },
    [exchangeStart, windowStartRef, setWindowStart],
  );

  return {
    scrollRef,
    innerRef,
    resultScrollRef,
    windowStart,
    onScroll,
    unpin,
    revealFrom,
    showInActivity,
    ensureTurnMounted,
  };
}
