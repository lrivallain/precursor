import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import type { MeetingSegment } from "../lib/types";
import { useBusyAction } from "../lib/useBusyAction";
import { clickSelect, invertSelection, selectFrom } from "../lib/transcriptSelection";
import { useConfirm } from "./ConfirmDialog";

export interface TranscriptSelection {
  active: boolean;
  setActive: (on: boolean) => void;
  selected: ReadonlySet<number>;
  busy: boolean;
  error: string | null;
  /** Row click: toggle, or select a range from the last click with Shift. */
  click: (id: number, shift: boolean) => void;
  /** Select this phrase and everything after it. */
  fromHere: (id: number) => void;
  selectAll: () => void;
  invert: () => void;
  clear: () => void;
  discard: () => Promise<void>;
}

/**
 * Batch selection over a live transcript, ending in one discard call. Lets a
 * user cut the tail of a recording they forgot to stop, or any set of phrases.
 */
export function useTranscriptSelection(
  sessionId: number,
  segments: readonly MeetingSegment[],
  onDiscarded: (removed: ReadonlySet<number>) => void,
): TranscriptSelection {
  const confirmAction = useConfirm();
  const { busy, error, setError, run } = useBusyAction();
  const [active, setActiveState] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const anchorRef = useRef<number | null>(null);
  const order = useMemo(() => segments.map((s) => s.id), [segments]);

  const clear = useCallback(() => {
    setSelected(new Set());
    anchorRef.current = null;
  }, []);
  const setActive = useCallback(
    (on: boolean) => {
      setActiveState(on);
      setError(null);
      if (!on) clear();
    },
    [clear, setError],
  );
  useEffect(() => setActive(false), [sessionId, setActive]);

  const click = useCallback(
    (id: number, shift: boolean) => {
      // Read the anchor now: the updater runs later, after it has moved.
      const anchor = shift ? anchorRef.current : null;
      setSelected((prev) => clickSelect(order, prev, id, anchor));
      anchorRef.current = id;
    },
    [order],
  );
  const fromHere = useCallback(
    (id: number) => {
      setSelected((prev) => selectFrom(order, prev, id));
      anchorRef.current = id;
    },
    [order],
  );

  async function discard(): Promise<void> {
    const ids = order.filter((id) => selected.has(id));
    if (ids.length === 0) return;
    const ok = await confirmAction({
      title: "Discard transcript phrases",
      message: `Discard ${ids.length} selected phrase${ids.length === 1 ? "" : "s"}? This cannot be undone.`,
      confirmLabel: "Discard",
      variant: "danger",
    });
    if (!ok) return;
    // Phrases that never reached the server (negative ids) only exist locally.
    const saved = ids.filter((id) => id > 0);
    const done = await run(() =>
      saved.length ? api.meetings.discardSegments(sessionId, saved) : Promise.resolve(),
    );
    if (!done) return;
    onDiscarded(new Set(ids));
    clear();
  }

  return {
    active,
    setActive,
    selected,
    busy,
    error,
    click,
    fromHere,
    selectAll: () => setSelected(new Set(order)),
    invert: () => setSelected((prev) => invertSelection(order, prev)),
    clear,
    discard,
  };
}
